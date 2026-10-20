"""보고서에 넘길 사실을 다듬는다. 모델이 셈과 관계의 방향을 정하지 않게, 코드가 미리 정해서 준다.

평가 10판 1차에서 틀린 사실 26건 가운데 11건이 모델이 직접 세거나 더하거나 '가장 큰 것'을 고른 값, 7건이 관계의 방향을 거꾸로 쓴 것,
4건이 원 단위 금액을 조·억으로 옮기다 자릿수를 틀린 것이었다. 그래서
- 관계마다 이 회사 쪽에서 본 역할(role)을 글로 적는다
- 건수·합계·가장 큰 것은 summary 에 계산해 둔다
- 금액은 모두 '조·억 원' 글로 준다
"""
import re

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

    def receipt(r: dict, table_name: str):
        """두 회사 보고서에 모두 있는 지분이면 그 표의 접수번호만 붙인다 (다른 회사 보고서의 비율에 이 회사 접수번호를 달지 않게)."""
        for report in r.get("reports") or []:
            if report.get("table") == table_name:
                return [report["rcept_no"]], report.get("value")
        return r.get("rcept_no"), r.get("value")
    for r in holders.get("relations", []):
        mine = r.get("disclosed_by") != "주체가 공시"
        rcept, pct = receipt(r, "최대주주 현황" if mine else "타법인출자 현황")
        row = {"name": _name(r.get("subject")), "pct": pct, "shares": (r.get("detail") or {}).get("shares"),
               "date": r.get("as_of_date"), "rcept_no": rcept}
        if not mine:   # 그 회사가 자기 보고서(타법인출자 현황)에 적은 것뿐이다. 이 회사의 최대주주 표에는 없다
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


def _side(company_id: int, r: dict, mine: str, theirs: str) -> str:
    """이 회사 쪽에서 본 역할. 지주회사가 자회사의 일을 자기 이름으로 공시한 것(detail.subsidiary)은 자회사의 일이라고 밝힌다."""
    detail = r.get("detail") or {}
    if detail.get("subsidiary"):
        return f"자회사 {detail['subsidiary']}의 일을 이 회사가 대신 공시한 것. 이 회사 자신의 일이 아니다"
    return mine if (r.get("subject") or {}).get("company_id") == company_id else theirs


def _amount_text(cell: str) -> str:
    """정정 표의 원 단위 금액("114,802,880,000")은 '1,148억 원' 글로. 비율·날짜·문장은 그대로."""
    found = re.fullmatch(r"[-\s]*([\d,]{9,})\s*원?", cell)
    if found:
        return won(found.group(1).replace(",", "")) or cell
    return cell


def corrections(changes: list) -> list[str]:
    """정정 내역을 '항목: 정정 전 X → 정정 후 Y' 글로. 방향을 글에 박아 두어 모델이 뒤집지 않게 한다. 긴 설명 칸은 줄인다."""
    out = []
    for label, before, after in changes[:6]:
        before, after = _amount_text(str(before)), _amount_text(str(after))
        if before != after:
            out.append(f"{label}: 정정 전 {before[:80]} → 정정 후 {after[:80]}")
    return out


def _receipts(r: dict) -> str:
    value = r.get("rcept_no")
    return ", ".join(value) if isinstance(value, list) else str(value or "")


def _contract(company_id: int, r: dict) -> dict:
    detail = r.get("detail") or {}
    seller = (r.get("subject") or {}).get("company_id") == company_id
    item = {"role": _side(company_id, r, "이 회사가 판(수주한) 계약 — 이 회사가 공시", "이 회사가 산(발주한) 계약 — 상대 회사가 공시"),
            "counterparty": _name(r.get("object") if seller else r.get("subject")),
            "amount": won(r.get("value")), "contract_date": r.get("as_of_date"), "filed": r.get("disclosed_date"),
            "title": detail.get("title"), "kind": detail.get("kind"), "period": f"{detail.get('period_start') or ''}~{detail.get('period_end') or ''}",
            "ratio_pct_of_sales": detail.get("ratio_pct"), "rcept_no": r.get("rcept_no")}
    if detail.get("changes"):
        item["corrected"] = "정정 공시가 있었다. 금액은 정정된 뒤의 값이고 filed 는 마지막 정정의 접수일이다"
        item["corrections"] = corrections(detail["changes"])
    return {k: v for k, v in item.items() if v not in (None, "", "~")}


def _stake(company_id: int, r: dict, verb: str) -> dict:
    detail = r.get("detail") or {}
    return {k: v for k, v in {
        "role": _side(company_id, r, f"이 회사가 다른 회사 지분을 {verb}기로 한 결정 — 이 회사가 공시",
                      f"다른 회사가 이 회사 지분을 {verb}기로 한 결정 — 그 회사가 공시. 이 회사가 {verb}는 것이 아니다"),
        "verb": verb, "actor": _name(r.get("subject")), "target": _name(r.get("object")), "amount": won(r.get("value")),
        "shares": detail.get("shares"), "pct_after": detail.get("pct_after"),
        "decided": r.get("as_of_date"), "filed": r.get("disclosed_date"),
        "purpose": detail.get("purpose"), "rcept_no": r.get("rcept_no"),
        "corrections": corrections(detail["changes"]) if detail.get("changes") else None}.items() if v not in (None, "", [])}


def _termination(company_id: int, r: dict) -> dict:
    detail = r.get("detail") or {}
    seller = (r.get("subject") or {}).get("company_id") == company_id
    return {k: v for k, v in {
        "role": _side(company_id, r, "이 회사가 판 계약의 해지 — 이 회사가 공시", "이 회사가 산 계약의 해지 — 상대 회사가 공시"),
        "counterparty": _name(r.get("object") if seller else r.get("subject")), "title": detail.get("title"),
        "amount": won(r.get("value")), "terminated": r.get("as_of_date"), "filed": r.get("disclosed_date"),
        "reason": detail.get("reason"), "rcept_no": r.get("rcept_no")}.items() if v not in (None, "")}


def deals(company_id: int, supply: dict, acquisitions: dict, disposals: dict, capital_events: int, since: str = "",
          terminations: dict | None = None, collected_until: str | None = None) -> dict:
    """supply 는 잘리지 않은 전체 목록이어야 한다(조회 도구는 50건에서 자른다).
    계약일(지분 결정은 결의일)이 since 보다 이른 것은 옛 계약·결정의 정정 공시라 따로 둔다 (10판: 이것을 새 것으로 세거나 수를 줄여 썼다)."""
    rows = supply.get("relations", [])
    older = [r for r in rows if (r.get("as_of_date") or "9999") < since]
    rows = [r for r in rows if r not in older]
    contracts = [_contract(company_id, r) for r in rows]
    raw = {id(c): r.get("value") or 0 for c, r in zip(contracts, rows)}
    sold = [c for c in contracts if c["role"].startswith("이 회사가 판")]
    bought = [c for c in contracts if c["role"].startswith("이 회사가 산")]
    proxy = [c for c in contracts if c["role"].startswith("자회사")]
    biggest = max(contracts, key=lambda c: raw[id(c)], default=None)
    contracts.sort(key=lambda c: -raw[id(c)])
    stakes = [_stake(company_id, r, "사") for r in acquisitions.get("relations", [])] + [_stake(company_id, r, "팔") for r in disposals.get("relations", [])]
    old_stakes = [s for s in stakes if (s.get("decided") or "9999") < since]
    stakes = sorted((s for s in stakes if s not in old_stakes), key=lambda s: s.get("decided") or s.get("filed") or "", reverse=True)
    ended = sorted((_termination(company_id, r) for r in (terminations or {}).get("relations", [])), key=lambda e: e.get("filed") or "", reverse=True)
    return {
        "note": ROLE_NOTE + " 정정 공시는 corrections 의 '정정 전 → 정정 후' 방향대로 쓴다.",
        "since": since, "collected_until": collected_until,
        "summary": {"contracts": len(contracts), "contracts_note": f"계약일이 {since} 이후인 공급계약. 옛 계약의 정정 {len(older)}건은 따로 셌다",
                    "sold_by_this_company": len(sold), "bought_by_this_company": len(bought), "filed_for_subsidiaries": len(proxy),
                    "sold_total": won(sum(raw[id(c)] for c in sold)), "bought_total": won(sum(raw[id(c)] for c in bought)),
                    "largest_contract": biggest, "terminations": len(ended), "corrections_of_older_contracts": len(older),
                    "stake_decisions": len(stakes), "corrections_of_older_stake_decisions": len(old_stakes),
                    "capital_decisions_elsewhere": f"같은 기간 증자·사채·자기주식 같은 자본 결정 {capital_events}건은 '자본 변동과 주주 환원' 항목에 있다" if capital_events else None},
        "contracts": contracts, "stake_decisions": stakes, "terminations": ended,
        "corrections_of_older_stake_decisions": old_stakes,
        "corrections_of_older_contracts": [{"counterparty": _contract(company_id, r).get("counterparty"), "title": (r.get("detail") or {}).get("title"),
                                            "amount": won(r.get("value")), "contract_date": r.get("as_of_date"),
                                            "filed": r.get("disclosed_date"), "rcept_no": r.get("rcept_no"),
                                            "corrections": corrections((r.get("detail") or {}).get("changes") or []),
                                            "role": "1년보다 오래된 계약을 이 기간에 정정한 공시. 새 계약이 아니다"} for r in older],
    }


def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침이 있으면 '이·을', 없으면 '가·를'. 한글로 끝나지 않으면(영문, 괄호) 받침 없는 쪽으로 본다."""
    last = (word or "").rstrip()[-1:]
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return word + (with_final if has_final else without_final)


def _amount_changed(lines: list[str]) -> str | None:
    """정정 내역 가운데 계약금액 줄(항목 이름에 '계약금액'이 있는 것)의 '정정 전 → 정정 후'."""
    for line in lines or []:
        label, _, change = line.partition(": 정정 전 ")
        if "계약금액" in label and change:
            return "정정 전 " + change
    return None


def _shares(value) -> str:
    try:
        return f"{int(float(str(value).replace(',', ''))):,}주"
    except (TypeError, ValueError):
        return ""


def _dates(first: str | None, first_label: str, filed: str | None) -> str:
    """'2026-04-23 결의, 2026-05-21 공시'. 두 날짜가 같으면 한 번만."""
    if first and filed and first != filed:
        return f"{first} {first_label}, {filed} 공시"
    if first:
        return f"{first} {first_label}·공시" if first == filed else f"{first} {first_label}"
    return f"{filed} 공시" if filed else "날짜 없음"


def _contract_line(c: dict) -> str:
    who = c["role"].split(" — ")[0].replace("이 회사가 판(수주한) 계약", "수주").replace("이 회사가 산(발주한) 계약", "발주(상대 회사가 공시)")
    if c["role"].startswith("자회사"):
        who = "자회사 계약을 대신 공시"
    parts = [_dates(c.get("contract_date"), "계약", c.get("filed")), who]
    if c.get("counterparty") and c["counterparty"].strip("- "):
        parts.append(f"상대 {c['counterparty']}")
    if c.get("title"):
        parts.append(f"'{c['title']}'")
    parts.append(c.get("amount") or "금액 없음")
    if c.get("ratio_pct_of_sales"):
        whose = "공시한 상대 회사의 최근 매출액" if c["role"].startswith("이 회사가 산") else "최근 매출액"
        parts.append(f"{whose} 대비 {c['ratio_pct_of_sales']}%")
    if c.get("corrected"):
        parts.append("정정 공시 있음(정정 뒤 금액)")
    return " · ".join(parts) + f" (접수번호 {_receipts(c)})"


def _stake_line(s: dict) -> str:
    what = f"{_josa(s.get('actor') or '', '이', '가')} {s.get('target')} 지분"
    if s.get("shares"):
        what += f" {_shares(s['shares'])}"
    what = _josa(what, "을", "를") + (f" {s['amount']}에" if s.get("amount") else "")
    what += " 취득하기로 결정" if s.get("verb") == "사" else " 처분하기로 결정"
    if s.get("pct_after") not in (None, ""):
        what += f"(그 뒤 지분율 {s['pct_after']}%)"
    whose = "이 회사가 공시" if s["role"].startswith("이 회사가") else ("자회사 일을 대신 공시" if s["role"].startswith("자회사") else f"{s.get('actor')}의 공시, 이 회사의 결정이 아님")
    corrected = ", 정정 공시" if s.get("corrections") else ""
    return f"{_dates(s.get('decided'), '결의', s.get('filed'))}{corrected} · {what} — {whose} (접수번호 {_receipts(s)})"


def deals_text(d: dict, shown: int = 8) -> str:
    """'최근 1년의 계약과 결정' 항목의 본문. 모델이 세거나 고르거나 날짜를 옮기지 않게 코드가 쓴다 (10판 처음 보는 5곳의 틀린 사실이 여기 몰렸다)."""
    s, since = d["summary"], d.get("since") or ""
    out = []
    if s["contracts"]:
        head = f"- **공급계약 {s['contracts']}건** (계약일 {since} 이후):"
        bits = []
        if s["sold_by_this_company"]:
            bits.append(f"이 회사가 수주한 계약 {s['sold_by_this_company']}건, 합계 {s['sold_total']}")
        if s["bought_by_this_company"]:
            bits.append(f"이 회사가 발주한 계약(상대 회사가 공시) {s['bought_by_this_company']}건, 합계 {s['bought_total']}")
        if s["filed_for_subsidiaries"]:
            bits.append(f"자회사의 계약을 이 회사가 대신 공시한 것 {s['filed_for_subsidiaries']}건")
        out.append(head + " " + "; ".join(bits) + ".")
        big = s.get("largest_contract")
        if big and big.get("amount"):
            out.append(f"- **가장 큰 계약**: {_contract_line(big)}")
        listed = d["contracts"][:shown]
        label = "금액이 큰 순서로 전부" if len(d["contracts"]) <= shown else f"금액이 큰 순서로 {len(listed)}건 (나머지 {len(d['contracts']) - len(listed)}건은 카드와 접수번호로 볼 수 있다)"
        out.append(f"- {label}:")
        out += [f"  - {_contract_line(c)}" for c in listed]
    else:
        out.append(f"- **공급계약**: 계약일 {since} 이후의 단일판매·공급계약 공시 없음.")
    if d["terminations"]:
        out.append(f"- **공급계약 해지 {len(d['terminations'])}건**:")
        for e in d["terminations"][:shown]:
            reason = f" · 사유: {e['reason'][:60]}" if e.get("reason") else ""
            amount = f" · 해지금액 {e['amount']}" if e.get("amount") else ""
            title = f" · '{e['title']}'" if e.get("title") else ""
            party = f" · 상대 {e['counterparty']}" if (e.get("counterparty") or "").strip("- ") else ""
            out.append(f"  - {_dates(e.get('terminated'), '해지', e.get('filed'))}{party}{title}{amount}{reason} (접수번호 {_receipts(e)})")
    if d["corrections_of_older_contracts"]:
        older = d["corrections_of_older_contracts"]
        out.append(f"- **옛 계약의 정정 공시 {len(older)}건**: 계약일이 {since}보다 이른 계약을 이 기간에 정정한 공시다. 새 계약이 아니라 위 건수에 넣지 않았다. 금액이 바뀐 것:")
        changed = [o for o in older if _amount_changed(o.get("corrections"))]
        for o in changed[:3]:
            out.append(f"  - {o.get('contract_date')} 계약 '{o.get('title') or ''}' · {o.get('filed')} 정정 · 계약금액 {_amount_changed(o['corrections'])} (접수번호 {_receipts(o)})")
        if len(changed) > 3:
            out.append(f"  - 계약금액을 바꾼 정정은 모두 {len(changed)}건이다.")
        if not changed:
            out[-1] = out[-1].replace(" 금액이 바뀐 것:", " 계약금액을 바꾼 정정은 없다.")
    if d["stake_decisions"]:
        out.append(f"- **다른 회사 지분의 취득·처분 결정 {len(d['stake_decisions'])}건** (결의일 {since} 이후, 결정이지 실행 결과가 아니다):")
        out += [f"  - {_stake_line(x)}" for x in d["stake_decisions"][:shown]]
    if d["corrections_of_older_stake_decisions"]:
        old = d["corrections_of_older_stake_decisions"]
        out.append(f"- **옛 지분 결정의 정정 공시 {len(old)}건**: 결의일이 {since}보다 이른 결정을 이 기간에 정정한 공시다. 최근 1년의 새 결정이 아니다.")
        out += [f"  - {_stake_line(x)}" for x in old[:3]]
    if s.get("capital_decisions_elsewhere"):
        out.append(f"- {s['capital_decisions_elsewhere']}.")
    if d.get("collected_until"):
        out.append(f"- 이 DB의 공급계약·해지 공시는 {d['collected_until']} 접수분까지 모았다. 그 뒤 공시는 여기에 없다.")
    return "\n".join(out)


def capital(events: dict, dividends: dict) -> dict:
    years = dividends.get("years", [])
    none = bool(years) and all(not (y.get("dps_common_won") or 0) and y.get("cash_total") in (None, "현금배당 없음") for y in years)
    return {
        "note": ROLE_NOTE + " by_kind 의 건수와 합계를 쓴다. 자기주식은 직접 취득(direct)과 신탁계약(trust)을 따로 쓴다.",
        "decisions_by_kind": events.get("by_kind"),
        "decisions": [{("filed_date_maybe_last_correction" if k == "date" else k): v for k, v in e.items()} for e in events.get("events", [])[:15]],
        "decision_note": events.get("note"),
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
