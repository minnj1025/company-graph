"""질문 하나를 받아 agent_tools 의 도구로 DB를 조회하고 답하는 Agent.

Claude API를 부른다(유료). 실행: python -m company_graph.agent "질문" [--model claude-opus-5-5] [--as-of 2026-09-30]

- 모델이 도구를 고르고, 도구는 이 프로세스 안에서 돈다. SQL은 모델에게 주지 않는다
- 답에 적힌 접수번호가 실제로 도구 결과에 있었는지 끝에서 확인한다 (지어낸 근거 막기)
- 한 질문에 쓴 토큰과 도구 호출을 같이 돌려준다 (평가와 비용 계산에 쓴다)
"""
import argparse
import json
import re
import time
from datetime import date

import anthropic

from . import agent_tools
from .config import secret
from .db import session

MODEL = "claude-opus-5-5"
MAX_TURNS = 12
SYSTEM = """당신은 한국 상장사의 공시(DART)에서 뽑은 기업 관계 DB를 조회해 사내 분석가의 질문에 답합니다.

답하는 법
- 사실은 반드시 도구로 조회해서 답합니다. 기억으로 답하지 않습니다. 도구 결과에 없는 내용은 쓰지 않습니다.
- 기업은 먼저 find_company 로 찾고, 그 company_id 로 조회합니다.
- as_of 는 "언제까지 공시된 것을 볼 것인가"입니다. 질문이 "그때 알려져 있던 것"을 물을 때만 과거 날짜로 바꾸고, 그 밖에는 사용자 메시지의 조회 시점을 씁니다.
- "2025년 말 기준 지분"처럼 기준일을 말하는 질문은 조회 시점을 과거로 돌리지 않습니다. 2025년 말의 지분은 2026년 3월쯤 나오는 사업보고서에 실리기 때문입니다. 결과의 as_of_date 가 질문의 기준일과 같은지 확인하고, 다르면 어느 기준일의 값인지 밝힙니다.
- 숫자(금액, 지분율, 날짜)는 도구 결과에 적힌 그대로 옮깁니다. 어림하거나 반올림하지 않습니다.
- 사실마다 근거 공시의 접수번호(rcept_no, 14자리)를 붙입니다.
- "모두 들어라" 같은 질문에는 결과의 total 과 truncated 를 확인하고, 건수를 밝히고 빠짐없이 적습니다.
- "그 기간에 나온 공시"를 세거나 나열하는 질문에는 include_superseded=true 로 조회합니다. 기본 조회는 지금 유효한 것만 주므로, 그 뒤에 다시 정정된 공시가 빠집니다. 나중에 정정된 건은 그렇다고 표시합니다.

없는 것과 모르는 것을 구분합니다
- 조회 결과가 비었으면 coverage 를 보고, 수집 범위 안이면 "공시된 것이 없다", 범위 밖이면 "이 DB는 그것을 모으지 않았다"고 답합니다.
- 주주는 최대주주와 그 특수관계인(개인 포함), 그리고 지분 보유를 자기 보고서에 적은 회사만 있습니다. 그 밖의 주주는 없습니다. 개인이 가진 다른 회사 지분은 이 DB로 알 수 없으니 지어내지 않습니다.
- 공시가 계약상대를 밝히지 않았으면(party_hidden) 상대를 추측하지 않습니다.
- 계약이 유효한지, 결정이 철회됐는지는 get_filings 로 정정·해지·철회 공시를 확인한 뒤 답합니다.

하지 않는 것
- 매수·매도·보유 판단, 주가 전망은 답하지 않습니다. 정중히 거절하고, 대신 조회해 줄 수 있는 사실(공시된 계약, 지분, 계열)을 제안합니다.
- 공시 사실을 묻는 질문은 투자와 관련돼 보여도 거절하지 않고 사실만 답합니다.

답은 한국어로, 결론부터 짧게 씁니다."""


def answer(question: str, *, model: str = MODEL, as_of: date | None = None, effort: str = "medium", client=None) -> dict:
    client = client or anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    when = as_of or date.today()
    messages = [{"role": "user", "content": f"조회 시점: {when.isoformat()}\n\n질문: {question}"}]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    calls, seen, started = [], set(), time.time()
    text, stop = "", None
    with session() as db:
        for _ in range(MAX_TURNS):
            response = client.messages.create(
                model=model, max_tokens=16000, system=SYSTEM, tools=agent_tools.TOOLS, messages=messages,
                cache_control={"type": "ephemeral"}, output_config={"effort": effort})
            for key in usage:
                usage[key] += getattr(response.usage, key, 0) or 0
            stop = response.stop_reason
            messages.append({"role": "assistant", "content": response.content})
            if stop != "tool_use":
                text = "\n".join(block.text for block in response.content if block.type == "text")
                break
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = agent_tools.call(db, block.name, dict(block.input))
                body = json.dumps(result, ensure_ascii=False, default=str)
                seen.update(re.findall(r"\b\d{14}\b", body))
                calls.append({"tool": block.name, "input": dict(block.input), "error": result.get("error"),
                              "total": result.get("total"), "chars": len(body)})
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": body,
                                "is_error": "error" in result})
            messages.append({"role": "user", "content": results})
    cited = set(re.findall(r"\b\d{14}\b", text))
    return {"question": question, "as_of": when.isoformat(), "model": model, "answer": text, "stop_reason": stop,
            "tool_calls": calls, "cited": sorted(cited), "cited_not_in_results": sorted(cited - seen),
            "usage": usage, "seconds": round(time.time() - started, 1)}


def main():
    parser = argparse.ArgumentParser(description="공시 관계 DB에 질문한다 (Claude API 사용, 유료)")
    parser.add_argument("question")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--effort", default="medium")
    args = parser.parse_args()
    out = answer(args.question, model=args.model, as_of=args.as_of, effort=args.effort)
    print(out["answer"])
    print("\n---")
    for call in out["tool_calls"]:
        print(f"도구 {call['tool']} {json.dumps(call['input'], ensure_ascii=False)} → "
              f"{call['error'] or ('' if call['total'] is None else str(call['total']) + '건 ')}({call['chars']}자)")
    if out["cited_not_in_results"]:
        print("경고: 도구 결과에 없는 접수번호를 인용함:", ", ".join(out["cited_not_in_results"]))
    print(f"모델 {out['model']} | {out['seconds']}초 | 토큰 {out['usage']} | 종료 {out['stop_reason']}")


if __name__ == "__main__":
    main()
