"""보고서에 넘길 사실을 다듬는다. 모델이 셈과 관계의 방향을 정하지 않게, 코드가 미리 정해서 준다.

평가 10판 1차에서 틀린 사실 26건 가운데 11건이 모델이 직접 세거나 더하거나 '가장 큰 것'을 고른 값, 7건이 관계의 방향을 거꾸로 쓴 것,
4건이 원 단위 금액을 조·억으로 옮기다 자릿수를 틀린 것이었다. 그래서
- 관계마다 이 회사 쪽에서 본 역할(role)을 글로 적는다
- 건수·합계·가장 큰 것은 summary 에 계산해 둔다
- 금액은 모두 '조·억 원' 글로 준다
"""
from .financials import readable

ROLE_NOTE = ("role 은 이 회사 쪽에서 본 관계다. 그대로 옮기고 뒤집지 않는다. 건수·합계·가장 큰 것은 summary 의 값만 쓰고 직접 세거나 더하지 않는다. "
             "서로 다른 종류(예: 자기주식 직접 취득과 신탁계약)의 금액을 더하지 않는다.")


def won(value) -> str | None:
    """원 단위 정수 → '4,711억 원'."""
    if value in (None, "") or value == 0:
        return None
    try:
        return readable(float(value) / 1e8)
    except (TypeError, ValueError):
        return None


def _name(side: dict | None) -> str | None:
    return (side or {}).get("name")


def governance(company_id: int, holders: dict, investments: dict, major: dict) -> dict:
    table, others = [], []
    for r in holders.get("relations", []):
        row = {"name": _name(r.get("subject")), "pct": r.get("value"), "shares": (r.get("detail") or {}).get("shares"),
               "date": r.get("as_of_date"), "rcept_no": r.get("rcept_no")}
        if r.get("disclosed_by") == "주체가 공시":   # 그 회사가 자기 보고서(타법인출자 현황)에 적은 것뿐이다. 이 회사의 최대주주 표에는 없다
            others.append({**row, "role": "다른 회사가 자기 사업보고서(타법인출자 현황)에 이 회사 주식을 가졌다고 적은 것. 최대주주·특수관계인이 아니다"})
        else:
            table.append({**row, "role": "이 회사의 최대주주 또는 특수관계인 (이 회사 사업보고서의 최대주주 현황)"})
    invested = [{"name": _name(r.get("object")), "pct": r.get("value"), "listed": ((r.get("object") or {}).get("market") or "비상장"),
                 "date": r.get("as_of_date"), "rcept_no": r.get("rcept_no"),
                 "role": "이 회사가 지분을 가진 회사 (이 회사 사업보고서의 타법인출자 현황)"} for r in investments.get("relations", [])]
    invested.sort(key=lambda r: -(r["pct"] or 0))
    full = [h for h in major.get("holders", []) if h.get("status") == "5% 이상"]
    return {
        "note": ROLE_NOTE + " 지분율은 기준일(date)과 함께 쓴다. 5% 보고의 비율은 대표보고자와 특별관계자를 합친 값이라 최대주주 현황과 다를 수 있다.",
        "largest_holder_table": {"total": holders.get("holder_table_total"), "rows": table[:15]},
        "others_reporting_this_company_shares": others[:8],
        "investments": invested[:15],
        "major_holders_5pct": [{"reporter": h["reporter"], "pct": h["pct"], "date": h["date"], "reason": h.get("reason"), "rcept_no": h["rcept_no"],
                                "role": "이 회사 주식을 5% 이상 가졌다고 보고한 자 (5% 대량보유 보고)"} for h in full],
        "summary": {"largest_holder_rows": len(table), "investments": investments.get("total", len(invested)),
                    "investments_100pct": sum(1 for r in invested if (r["pct"] or 0) >= 99.995),
                    "major_holders_5pct": len(full)},
    }


def _contract(company_id: int, r: dict) -> dict:
    detail = r.get("detail") or {}
    seller = (r.get("subject") or {}).get("company_id") == company_id
    item = {"role": "이 회사가 판(수주한) 계약 — 이 회사가 공시" if seller else "이 회사가 산(발주한) 계약 — 상대 회사가 공시",
            "counterparty": _name(r.get("object") if seller else r.get("subject")),
            "amount": won(r.get("value")), "date": r.get("as_of_date") or r.get("disclosed_date"),
            "title": detail.get("title"), "kind": detail.get("kind"), "period": f"{detail.get('period_start') or ''}~{detail.get('period_end') or ''}",
            "ratio_pct_of_sales": detail.get("ratio_pct"), "rcept_no": r.get("rcept_no")}
    changes = detail.get("changes") or []
    if changes:
        item["corrections"] = [f"{c[0]}: 정정 전 {c[1]} → 정정 후 {c[2]}" for c in changes if len(c) >= 3][:4]
    return {k: v for k, v in item.items() if v not in (None, "", "~")}


def _stake(company_id: int, r: dict, verb: str) -> dict:
    detail = r.get("detail") or {}
    actor = (r.get("subject") or {}).get("company_id") == company_id
    return {k: v for k, v in {
        "role": (f"이 회사가 다른 회사 지분을 {verb}기로 한 결정 — 이 회사가 공시" if actor
                 else f"다른 회사가 이 회사 지분을 {verb}기로 한 결정 — 그 회사가 공시. 이 회사가 {verb}는 것이 아니다"),
        "actor": _name(r.get("subject")), "target": _name(r.get("object")), "amount": won(r.get("value")),
        "shares": detail.get("shares"), "pct_after": detail.get("pct_after"), "date": r.get("as_of_date") or r.get("disclosed_date"),
        "purpose": detail.get("purpose"), "rcept_no": r.get("rcept_no")}.items() if v not in (None, "")}


def deals(company_id: int, supply: dict, acquisitions: dict, disposals: dict, capital_events: int) -> dict:
    contracts = [_contract(company_id, r) for r in supply.get("relations", [])]
    raw = {id(c): r.get("value") or 0 for c, r in zip(contracts, supply.get("relations", []))}
    sold = [c for c in contracts if c["role"].startswith("이 회사가 판")]
    bought = [c for c in contracts if c["role"].startswith("이 회사가 산")]
    biggest = max(contracts, key=lambda c: raw[id(c)], default=None)
    contracts.sort(key=lambda c: -raw[id(c)])
    stakes = [_stake(company_id, r, "사") for r in acquisitions.get("relations", [])] + [_stake(company_id, r, "팔") for r in disposals.get("relations", [])]
    return {
        "note": ROLE_NOTE + " 정정 공시는 corrections 의 '정정 전 → 정정 후' 방향대로 쓴다.",
        "summary": {"contracts": len(contracts), "sold_by_this_company": len(sold), "bought_by_this_company": len(bought),
                    "sold_total": won(sum(raw[id(c)] for c in sold)), "bought_total": won(sum(raw[id(c)] for c in bought)),
                    "largest_contract": biggest, "stake_decisions": len(stakes),
                    "capital_decisions_elsewhere": f"같은 기간 증자·사채·자기주식 같은 자본 결정 {capital_events}건은 '자본 변동과 주주 환원' 항목에 있다" if capital_events else None},
        "contracts": contracts[:15], "stake_decisions": stakes[:10],
    }


def capital(events: dict, dividends: dict) -> dict:
    years = dividends.get("years", [])
    none = bool(years) and all(not (y.get("dps_common_won") or 0) and y.get("cash_total") in (None, "현금배당 없음") for y in years)
    return {
        "note": ROLE_NOTE + " by_kind 의 건수와 합계를 쓴다. 자기주식은 직접 취득(direct)과 신탁계약(trust)을 따로 쓴다.",
        "decisions_by_kind": events.get("by_kind"), "decisions": events.get("events", [])[:15], "decision_note": events.get("note"),
        "dividends": years, "no_cash_dividend": "2023~2025 사업연도 현금배당 없음 (사업보고서 배당 표의 값이 비어 있음)" if none else None,
        "dividend_note": dividends.get("empty"), "buybacks_by_year": dividends.get("buybacks"),
        "treasury_cancellations": dividends.get("treasury_cancellations"),
    }


def financials(found: dict) -> dict:
    """가장 최근 분기와 그 전년 같은 분기를 나란히 준다(증감률만 쓰지 않게)."""
    quarters = found.get("quarters", [])
    latest = next((q for q in reversed(quarters) if q.get("revenue") or q.get("operating_income")), None)
    before = None
    if latest:
        before = next((q for q in quarters if q.get("fiscal_year") == latest["fiscal_year"] - 1 and q.get("quarter") == latest["quarter"]), None)
    return {**found, "latest_quarter": latest, "same_quarter_last_year": before,
            "note": "latest_quarter 와 same_quarter_last_year 의 금액을 함께 쓴다. 금액은 이미 조·억 원 글이다."}


def insiders(found: dict) -> dict:
    t = found.get("totals") or {}
    return {**found, "summary": {"reports_total": t.get("increased_reports", 0) + t.get("decreased_reports", 0) + t.get("unchanged_reports", 0), **t},
            "note": ROLE_NOTE + " 보고 건수는 summary.reports_total 이다. 날짜는 보고 접수일이다."}
