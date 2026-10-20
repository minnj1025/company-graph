"""재무 주요계정을 공시 안의 숫자끼리 맞춰 본다. 다른 출처와 대조하는 것이 아니라, 같은 보고서들 안에서 서로 맞아야 하는 값이 맞는지 본다.

1. 자산총계 = 부채총계 + 자본총계 (같은 보고서)
2. 누적 = 석 달 값의 합: 반기 누적 = 1분기 + 2분기, 3분기 누적 = 반기 누적 + 3분기 (같은 해의 보고서들)
3. 다시 작성: 다음 해 보고서가 적은 전년 같은 기간 값이 이 해의 보고서 값과 다른가 (회계 변경, 사업 분할 등. 틀린 것이 아니라 4분기 계산을 막는다)
4. 4분기 계산 값의 매출이 0보다 작은가 (연간과 분기의 기준이 어긋났다는 신호)

실행: python -m company_graph.financials check
"""
from collections import Counter, defaultdict

from sqlalchemy import select

from .db import Financial, session
from .financials import RESTATED, regroup, restated

TOLERANCE = 0.005


def _near(a, b) -> bool:
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b), 1)


def main() -> None:
    counts: Counter = Counter()
    examples = defaultdict(list)
    with session() as db:
        rows = list(db.scalars(select(Financial)))
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.company_id, row.basis)].append(row)
        if None not in (row.total_assets, row.total_liabilities, row.total_equity):
            counts["balance_checked"] += 1
            if not _near(row.total_assets, row.total_liabilities + row.total_equity):
                counts["balance_off"] += 1
                examples["balance_off"].append((row.company_id, row.fiscal_year, row.report, row.basis))
    by = {key: regroup(items) for key, items in grouped.items()}
    for (company_id, basis), reports in by.items():
        for year in {y for y, _ in reports}:
            q1, half, q3, annual = (reports.get((year, r)) for r in ("q1", "half", "q3", "annual"))
            for key in ("revenue", "operating_income"):
                if q1 and half and None not in (q1.__dict__.get(key), half.__dict__.get(key), half.__dict__.get(f"{key}_ytd")):
                    counts["ytd_checked"] += 1
                    if not _near(getattr(half, f"{key}_ytd"), getattr(q1, key) + getattr(half, key)):
                        counts["ytd_off"] += 1
                        examples["ytd_off"].append((company_id, year, "half", key))
                if half and q3 and None not in (getattr(half, f"{key}_ytd"), getattr(q3, key), getattr(q3, f"{key}_ytd")):
                    counts["ytd_checked"] += 1
                    if not _near(getattr(q3, f"{key}_ytd"), getattr(half, f"{key}_ytd") + getattr(q3, key)):
                        counts["ytd_off"] += 1
                        examples["ytd_off"].append((company_id, year, "q3", key))
            if annual and q3:
                counts["q4_possible"] += 1
                if restated(reports, year):
                    counts["q4_restated"] += 1
                    examples["q4_restated"].append((company_id, year, basis))
                elif annual.revenue is not None and q3.revenue_ytd is not None and annual.revenue - q3.revenue_ytd < 0:
                    counts["q4_negative_revenue"] += 1
                    examples["q4_negative_revenue"].append((company_id, year, basis))
    print(f"줄 {len(rows)} (회사×기준 {len(by)})")
    print(f"1. 자산 = 부채 + 자본: {counts['balance_checked']}줄 중 어긋남 {counts['balance_off']} ({counts['balance_off'] / max(counts['balance_checked'], 1):.2%})")
    print(f"2. 누적 = 석 달의 합: {counts['ytd_checked']}건 중 어긋남 {counts['ytd_off']} ({counts['ytd_off'] / max(counts['ytd_checked'], 1):.2%})")
    print(f"3. 다시 작성되어 4분기를 계산하지 않음: {counts['q4_possible']}건 중 {counts['q4_restated']} ({counts['q4_restated'] / max(counts['q4_possible'], 1):.2%}, 기준 {RESTATED:.0%})")
    print(f"4. 계산한 4분기 매출이 음수: {counts['q4_negative_revenue']}")
    for name, items in examples.items():
        print(f"   {name} 예: {items[:5]}")
