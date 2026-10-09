"""평가 3판의 이어지는 대화 문항. 첫 질문의 답을 받고, 그 답을 앞선 대화로 넘겨 둘째 질문을 묻는다. (Claude API, 유료)

둘째 질문은 "그중", "그 회사"처럼 앞을 가리킨다. 보는 것은 세 가지다:
가리키는 대상을 맞게 잡았는가, 앞선 답의 숫자를 옮기지 않고 이번에 다시 조회했는가, 답이 맞는가.

실행: python -m eval.v3.followups [--model claude-haiku-5-5] [--only F01,F02]
      → eval/v3/agent/<모델>/F01.json (first: 첫 질문의 결과, 나머지: 둘째 질문의 결과)
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from company_graph import agent

ROOT = Path(__file__).parent
AS_OF = date(2026, 10, 8)

CASES = [
    ("F01", "전기차 배터리 분리막을 만드는 회사와 매출 비중은?", "그중 유가증권시장에 상장된 곳의 최대주주는 누구야?"),
    ("F02", "카카오가 지분을 가진 상장사는?", "그중 지분율이 가장 높은 곳은 어디고 몇 %야?"),
    ("F03", "삼성전자의 최대주주는 누구야?", "그 회사의 최대주주는 누구고 지분율은 얼마야?"),
    ("F04", "현대모비스에 올해 공급계약을 공시한 회사는?", "그중 올해 새로 계약을 공시한 회사가 현대모비스와 맺은 계약은 몇 건이고 금액은 각각 얼마야?"),
    ("F05", "한화오션이 올해 공시한 공급계약을 금액이 큰 순서로 알려줘", "그중 가장 큰 계약의 계약 종료일은 언제야?"),
    ("F06", "SK하이닉스의 최대주주는 누구야?", "그 회사가 지분을 가진 다른 상장사는 어디야?"),
    ("F07", "에코프로비엠은 어떤 회사야?", "그 회사의 최대주주가 지분을 가진 다른 상장사는 어디야?"),
    ("F08", "LG화학의 최대주주는 누구고 지분율은 얼마야?", "LG에너지솔루션은?"),
    ("F09", "넷마블은 뭘 팔아서 돈을 벌어?", "그럼 지금 사도 될까?"),
    ("F10", "카카오의 계열회사 가운데 상장사는 어디야?", "그중 카카오가 가진 지분율이 가장 낮은 곳은 어디고 몇 %야?"),
]


def run(case, model: str, out_dir: Path):
    number, first_question, second_question = case
    first = agent.answer(first_question, model=model, as_of=AS_OF)
    second = agent.answer(second_question, model=model, as_of=AS_OF, history=[(first_question, first["answer"])])
    second["number"] = number
    second["first"] = {"question": first_question, "answer": first["answer"], "tool_calls": first["tool_calls"], "usage": first["usage"]}
    (out_dir / f"{number}.json").write_text(json.dumps(second, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"{number} 첫 질문 도구 {len(first['tool_calls'])}회 → 둘째 질문 도구 {len(second['tool_calls'])}회, 없는 근거 {len(second['cited_not_in_results'])}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="claude-haiku-5-5")
    parser.add_argument("--only", default="")
    parser.add_argument("--tag", default="", help="결과 폴더 이름 뒤에 붙일 말. 고친 뒤에 다시 돌린 결과를 따로 둘 때 쓴다 (--tag=-after)")
    args = parser.parse_args()
    only = set(filter(None, args.only.split(",")))
    out_dir = ROOT / "agent" / (args.model + args.tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = [case for case in CASES if not only or case[0] in only]
    with ThreadPoolExecutor(3) as pool:
        for line in pool.map(lambda case: run(case, args.model, out_dir), cases):
            print(line, flush=True)


if __name__ == "__main__":
    main()
