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
        if len(cells) >= 3 and re.fullmatch(r"Q\d\d", cells[0]):
            rows.append((cells[0], cells[1], cells[2]))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=agent.MODEL)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    only = set(filter(None, args.only.split(",")))
    out_dir = ROOT / "agent" / args.model
    out_dir.mkdir(parents=True, exist_ok=True)
    total = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    for number, question, gold in questions():
        if only and number not in only:
            continue
        result = agent.answer(question, model=args.model, as_of=AS_OF)
        result["number"], result["gold"] = number, gold
        (out_dir / f"{number}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        for key in total:
            total[key] += result["usage"][key]
        print(f"{number} {result['seconds']}초 도구 {len(result['tool_calls'])}회 "
              f"없는 근거 {len(result['cited_not_in_results'])} 종료 {result['stop_reason']}", flush=True)
    print("합계", total)


if __name__ == "__main__":
    main()
