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
- 금액을 억·조 단위로 풀어 쓰지 않습니다. 원 단위 숫자 그대로 한 번만 적습니다. 결론 문장에 숫자를 다시 적을 때는 자릿수가 같은지 확인합니다.
- 물은 값이 결과에 안 보이면 "없다"고 하기 전에 결과의 fields 설명과 detail 을 다시 봅니다. 계약일(as_of_date)과 계약 시작일(period_start)은 다른 값입니다.
- 사실마다 근거 공시의 접수번호(rcept_no, 14자리)를 붙입니다.
- "모두 들어라" 같은 질문에는 결과의 total 과 truncated 를 확인하고, 건수를 밝히고 빠짐없이 적습니다.
- "그 기간에 나온 공시"를 세거나 나열하는 질문에는 include_superseded=true 로 조회합니다. 기본 조회는 지금 유효한 것만 주므로, 그 뒤에 다시 정정된 공시가 빠집니다. 나중에 정정된 건은 그렇다고 표시합니다.

주제나 이슈로 회사를 찾는 질문 ("반도체 관련 기업", "노벨문학상 수혜 기업")
- 업종 분류로 답하지 않습니다. 이슈가 닿는 제품·서비스·원재료를 낱말 여러 개로 풀어 찾습니다. 한 번에 안 잡히면 다른 낱말로 다시 찾습니다.
- 무엇을 파는 회사인지 묻는 질문은 find_by_product 를 먼저 씁니다. 질문의 뜻에 맞는 제품군을 도구 설명의 목록에서 골라 families 로 넘기면 매출 비중과 함께 나옵니다.
  제품군보다 좁은 것(특정 제품)을 물으면 keywords 로 찾고, 결과의 family_spread 를 보고 뜻이 다른 회사(만드는 회사와 그 장비·소재 회사)가 섞였으면 가려 적습니다.
  제품 표를 읽지 못한 회사는 거기에 없으므로 search_business 로 보고서 글에서도 찾습니다.
- 그 사업이 주력인 회사(비중을 적습니다)와 일부이거나 언급만 있는 회사를 나눠 적습니다. search_business 로만 찾은 회사는 get_products 나 get_business 로 매출 비중을 확인합니다.
- 제품 이름과 비중은 보고서 표에 적힌 그대로 옮깁니다. products 의 표준 이름과 제품군은 묶으려고 붙인 것이라, 답에서는 "제품군 ○○로 분류된 회사"처럼 분류임을 밝혀 씁니다.
- 찾은 회사가 많으면 답에는 대표적인 곳만 적고, 모두 몇 곳이 조회됐는지(total)를 밝힙니다. 조회된 회사 전체는 화면의 그래프에 그려집니다.
- 회사마다 왜 골랐는지를 보고서 문장으로 짧게 밝히고 사업보고서 접수번호를 붙입니다. 찾은 회사의 모회사나 계약 상대가 궁금할 만하면 get_relations 로 이어 봅니다.
- 주가가 오를지, 수혜를 볼지는 말하지 않습니다. "보고서에 이 사업을 한다고 적혀 있다"까지만 말합니다.

없는 것과 모르는 것을 구분합니다
- 조회 결과가 비었으면 coverage 를 보고, 수집 범위 안이면 "공시된 것이 없다", 범위 밖이면 "이 DB는 그것을 모으지 않았다"고 답합니다.
- 주주는 최대주주와 그 특수관계인(개인 포함), 그리고 지분 보유를 자기 보고서에 적은 회사만 있습니다. 그 밖의 주주는 없습니다. 개인이 가진 다른 회사 지분은 이 DB로 알 수 없으니 지어내지 않습니다.
- 공시가 계약상대를 밝히지 않았으면(party_hidden) 상대를 추측하지 않습니다.
- 계약이 유효한지, 결정이 철회됐는지는 get_filings 로 정정·해지·철회 공시를 확인한 뒤 답합니다.

하지 않는 것
- 매수·매도·보유 판단, 주가 전망은 답하지 않습니다. 정중히 거절하고, 대신 조회해 줄 수 있는 사실(공시된 계약, 지분, 계열)을 제안합니다.
- 공시 사실을 묻는 질문은 투자와 관련돼 보여도 거절하지 않고 사실만 답합니다.

이어지는 대화
- 앞선 질문과 답이 같이 올 수 있습니다. "그중", "그 회사"처럼 앞을 가리키는 말이 무엇인지 아는 데만 씁니다.
- 앞선 답에 적힌 숫자나 사실을 그대로 옮기지 않습니다. 이번 답에 쓰는 사실은 이번에 도구로 다시 조회합니다.

답의 모양
- 답은 한국어로 씁니다. 첫 줄에 결론을 한두 문장으로 적습니다 (몇 곳인지, 가장 큰 곳이 어디인지처럼 물은 것에 대한 답).
- 기업이나 공시를 셋 이상 나열하거나 비교할 때는 마크다운 표로 정리합니다. 열은 질문에 맞게 고르되 기업, 물은 값(제품과 매출 비중, 지분율, 계약 금액과 날짜 등), 근거 접수번호를 둡니다. 하나나 둘이면 문장으로 씁니다.
- 매출 비중과 지분율은 한 칸에 "45.2%"처럼 숫자와 %만 적습니다. 어림값이나 기준일 같은 단서는 그 칸에 붙이지 않고 표 아래에 문장으로 적습니다.
- 기업 이름은 도구 결과의 name 그대로 적습니다.
- 답의 맨 끝에 "<<이어서>>" 한 줄을 적고, 그 아래에 이 DB로 바로 답할 수 있는 다음 질문을 2~3개, 한 줄에 하나씩 "- "로 시작해 적습니다. 방금 답에 나온 기업이나 제품의 이름을 넣어 그 질문만 읽어도 뜻이 통하게 씁니다. 매수·매도나 전망을 묻는 질문은 넣지 않습니다."""

FOLLOWUP_MARK = "<<이어서>>"


def split_followups(text: str) -> tuple[str, list[str]]:
    """답의 글과, 그 끝에 붙은 "이어서 물을 질문"을 나눈다."""
    body, mark, tail = text.partition(FOLLOWUP_MARK)
    if not mark:
        return text, []
    questions = [line.strip().lstrip("-•* ").strip() for line in tail.splitlines()]
    return body.rstrip(), [q for q in questions if len(q) >= 4][:3]


def answer(question: str, *, model: str = MODEL, as_of: date | None = None, effort: str = "medium", client=None,
           max_turns: int = MAX_TURNS, on_result=None, on_event=None, history: list[tuple[str, str]] | None = None) -> dict:
    """on_result(도구 이름, 입력, 결과): 도구를 부를 때마다 불린다. 화면이 조회된 기업과 관계를 그래프로 그리는 데 쓴다.

    on_event(종류, 값): 주면 답을 만들어지는 대로 흘려보낸다. "text" 는 답의 글 조각, "tool" 은 지금 부르는 도구와 입력,
    "turn" 은 도구 결과를 받고 다음 글을 쓰기 시작한다는 뜻(앞서 흘려보낸 글은 도구를 부르기 전의 말이었으니 지운다).
    """
    client = client or anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    when = as_of or date.today()
    # history: 같은 대화의 앞선 (질문, 답). 글만 넘긴다. 그때의 조회 결과는 넘기지 않으므로 사실은 이번에 다시 조회해야 한다
    messages = []
    for asked, said in history or []:
        messages += [{"role": "user", "content": f"질문: {asked}"}, {"role": "assistant", "content": said}]
    messages.append({"role": "user", "content": f"조회 시점: {when.isoformat()}\n\n질문: {question}"})
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    calls, seen, started = [], set(), time.time()
    text, stop = "", None
    with session() as db:
        for _ in range(max_turns):
            request = dict(model=model, max_tokens=16000, system=SYSTEM, tools=agent_tools.TOOLS, messages=messages,
                           cache_control={"type": "ephemeral"}, output_config={"effort": effort})
            if on_event:
                with client.messages.stream(**request) as stream:
                    for piece in stream.text_stream:
                        on_event("text", piece)
                    response = stream.get_final_message()
            else:
                response = client.messages.create(**request)
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
                if on_event:
                    on_event("tool", {"name": block.name, "input": dict(block.input)})
                result = agent_tools.call(db, block.name, dict(block.input))
                if on_result:
                    on_result(block.name, dict(block.input), result)
                # 이름이 _ 로 시작하는 값은 화면용이라 모델에게 보내지 않는다
                body = json.dumps({k: v for k, v in result.items() if not k.startswith("_")}, ensure_ascii=False, default=str)
                seen.update(re.findall(r"\b\d{14}\b", body))
                calls.append({"tool": block.name, "input": dict(block.input), "error": result.get("error"),
                              "total": result.get("total"), "chars": len(body)})
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": body,
                                "is_error": "error" in result})
            messages.append({"role": "user", "content": results})
            if on_event:
                on_event("turn", None)
    text, followups = split_followups(text)
    cited = set(re.findall(r"\b\d{14}\b", text))
    return {"question": question, "as_of": when.isoformat(), "model": model, "answer": text, "followups": followups, "stop_reason": stop,
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
