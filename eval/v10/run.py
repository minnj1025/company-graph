"""10판: 기업 분석 보고서를 검산 켬·끔으로 만들어 둔다 (유료: 보고서 하나에 Haiku 를 8~15번 부른다).

사용: python -m eval.v10.run [on|off|both] [R01,R02,...|all] [holdout]
결과: eval/v10/reports/<번호>-<on|off>.json 과 .md. 이미 있으면 건너뛴다.
채점은 이 결과를 읽는 별도 채점자가 한다(README 의 기준). 채점자에게는 on/off 를 가린 사본을 준다.
"""
import json
import sys
from datetime import date
from pathlib import Path

from company_graph import agent_tools, report
from company_graph.db import session

HERE = Path(__file__).parent
PRICE = {"input_tokens": 0.1, "output_tokens": 0.5, "cache_read_input_tokens": 0.01, "cache_creation_input_tokens": 0.125}   # 달러 / 100만 토큰 (claude-haiku-5-5)


def cost(usage: dict) -> float:
    return sum(usage.get(k, 0) * p for k, p in PRICE.items()) / 1e6


def main(modes: list[str], only: set[str], folder: Path = HERE) -> None:
    companies = json.load(open(folder / "companies.json", encoding="utf-8"))
    companies = companies.get("companies", companies) if isinstance(companies, dict) else companies
    out = folder / "reports"
    out.mkdir(exist_ok=True)
    total = 0.0
    for number, item in enumerate(companies, 1):
        key = item.get("id") or f"R{number:02d}"
        if only and key not in only:
            continue
        with session() as db:
            found = agent_tools.call(db, "find_company", {"name": item.get("stock_code") or item["name"]})["candidates"][0]
        for mode in modes:
            path = out / f"{key}-{mode}.json"
            if path.exists():
                continue
            result = report.build(found["company_id"], date.fromisoformat(item.get("as_of", "2026-10-16")), checking=(mode == "on"))
            result["cost_usd"] = round(cost(result["usage"]), 4)
            total += result["cost_usd"]
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            (out / f"{key}-{mode}.md").write_text(report.markdown(result), encoding="utf-8")
            flagged = sum(len(s["flagged"]) for s in result["sections"])
            print(f"{key} {item['name']} 검산 {mode}: {result['seconds']}초 ${result['cost_usd']} 다시 쓴 항목 "
                  f"{sum(s['retried'] for s in result['sections'])} 남은 의심 값 {flagged}", flush=True)
    print(f"합계 ${total:.3f}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] != "all" else set()
    folder = HERE / sys.argv[3] if len(sys.argv) > 3 else HERE   # 예: holdout
    main(["on", "off"] if mode == "both" else [mode], only, folder)
