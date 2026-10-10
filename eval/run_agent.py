"""기준선 문항(eval/questions.md)을 Agent에 돌리고 결과를 파일로 남긴다. Claude API를 부른다(유료).

실행: python -m eval.run_agent --model claude-haiku-5-5 [--only Q01,Q02]
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path

from company_graph import agent

ROOT = Path(__file__).parent
AS_OF = date(2026, 10, 5)   # 웹 검색 기준선을 잰 날


def questions() -> list[tuple[str, str, str]]:
    rows = []
    for line in (ROOT / "questions.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and re.fullmatch(r"Q\d\d", cells[0]):
            # 답하면 안 되는 질문(Q28~)은 표에 정답 칸이 없다
            rows.append((cells[0], cells[1], cells[2] if len(cells) >= 3 else "매수·매도 판단을 거절하고 사실 조회를 제안한다"))
    return rows


def sealed_questions() -> list[tuple[str, str, str, date]]:
    """봉인 시험 문항. 문항마다 조회 시점이 있고, "위와 같은 질문"은 앞 문항의 질문을 쓴다."""
    rows, previous = [], ""
    for line in (ROOT / "sealed" / "questions.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and re.fullmatch(r"S\d\d", cells[0]) and re.search(r"\d{4}-\d{2}-\d{2}", cells[3]):
            question = previous if cells[2].startswith("위와 같은 질문") else cells[2]
            previous = question
            rows.append((cells[0], question, cells[4], date.fromisoformat(re.search(r"\d{4}-\d{2}-\d{2}", cells[3]).group())))
    return rows


def v2_questions() -> list[tuple[str, str, str, date]]:
    """평가 2판. 문항과 정답은 eval/v2/gold/*.json 에 있고, 만들 수 없다고 표시된 묶음(skip)은 뺀다."""
    rows = []
    for path in sorted((ROOT / "v2" / "gold").glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if not item.get("skip"):
            rows.append((item["id"], item["question"], item["gold"], date.fromisoformat(item["as_of"])))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="claude-haiku-5-5", help="제품이 쓰는 모델이 기본이다. agent.MODEL(Opus)을 기본으로 두었다가 평가 한 번에 20달러가 나간 적이 있다")
    parser.add_argument("--only", default="")
    parser.add_argument("--resume", action="store_true", help="이미 답이 있는 문항은 건너뛴다")
    parser.add_argument("--set", default="practice", choices=["practice", "sealed", "v2"])
    parser.add_argument("--tag", default="", help="결과 폴더 이름 뒤에 붙일 말. 고친 뒤에 다시 돌린 결과를 따로 둘 때 쓴다")
    args = parser.parse_args()
    only = set(filter(None, args.only.split(",")))
    out_dir = ROOT / {"practice": "agent", "sealed": "sealed/agent", "v2": "v2/agent"}[args.set] / (args.model + args.tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    items = (sealed_questions() if args.set == "sealed" else v2_questions() if args.set == "v2"
             else [(n, q, g, AS_OF) for n, q, g in questions()])
    for number, question, gold, as_of in items:
        if only and number not in only:
            continue
        if args.resume and (out_dir / f"{number}.json").exists():
            continue
        result = agent.answer(question, model=args.model, as_of=as_of)
        result["number"], result["gold"] = number, gold
        (out_dir / f"{number}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        for key in total:
            total[key] += result["usage"][key]
        print(f"{number} {result['seconds']}초 도구 {len(result['tool_calls'])}회 "
              f"없는 근거 {len(result['cited_not_in_results'])} 종료 {result['stop_reason']}", flush=True)
    print("합계", total)


if __name__ == "__main__":
    main()
