"""Agent가 고르는 '보기'(시각화 카드).

Agent는 보기의 종류와 대상만 고르고, 카드에 들어갈 숫자는 여기서 조회 도구의 결과로 채운다.
그래서 카드의 값은 모델이 옮겨 적은 것이 아니라 DB에서 바로 온 것이다. 화면(web/src/Views.tsx)이 이 모양을 그린다.
"""
from datetime import date, timedelta

from sqlalchemy import func, select

from .db import PriceDaily
from .financials import readable

KINDS = {
    "ownership": "지분 구조도",
    "group": "계열 지도",
    "supply_flow": "공급망 흐름",
    "path": "연결 경로",
    "timeline": "공시 타임라인",
    "contracts": "계약 기간",
    "revenue": "매출 구성",
    "returns": "수익률 비교",
    "financials": "실적 추이",
    "health": "재무 체력",
    "compare": "실적 비교",
    "holders": "주주 분포",
    "insiders": "내부자 매매",
    "dilution": "희석 이력",
    "payout": "주주 환원",
}
TABLE_ONLY = ("exited", "share_class", "treasury")


def _who(side: dict) -> dict:
    return {"id": side.get("company_id"), "name": side.get("name"), "listed": side.get("market") in ("유가증권", "코스닥", "코넥스")}


def _receipts(rows) -> list[str]:
    out = []
    for row in rows:
        for no in row.get("rcept_no") or []:
            if no not in out:
                out.append(no)
    return out


def ownership(db, tools, company: dict, when: str) -> dict:
    holders = tools.get_relations(db, company["company_id"], when, "equity", "in")
    rows = [r for r in holders["relations"] if not any(r["detail"].get(k) for k in TABLE_ONLY) and (r["value"] or 0) > 0]
    top = rows[:8]
    out = []
    for r in top:
        holder = _who(r["subject"])
        up = []
        if holder["id"] and (r["value"] or 0) >= 5 and len([h for h in out if h["up"]]) < 3:   # 5% 이상 가진 회사 주주 셋까지는 그 위의 주주도 붙인다
            above = tools.get_relations(db, holder["id"], when, "equity", "in")
            up = [{**_who(a["subject"]), "pct": a["value"]} for a in above["relations"]
                  if not any(a["detail"].get(k) for k in TABLE_ONLY) and (a["value"] or 0) > 0][:3]
        out.append({**holder, "pct": r["value"], "role": r["detail"].get("relation_to_filer"), "rcept": (r["rcept_no"] or [None])[0], "up": up})
    invested = tools.get_relations(db, company["company_id"], when, "equity", "out")
    kids = [r for r in invested["relations"] if not r["detail"].get("exited") and (r["value"] or 0) > 0]
    # 상장사인 출자 대상을 먼저(지배 구조에서 눈여겨볼 곳), 그다음 지분율 큰 순
    kids.sort(key=lambda r: (r["object"].get("market") not in ("유가증권", "코스닥", "코넥스"), -(r["value"] or 0)))
    return {"holders": out, "more_holders": max(0, len(rows) - len(top)),
            "investees": [{**_who(r["object"]), "pct": r["value"], "purpose": r["detail"].get("purpose"), "rcept": (r["rcept_no"] or [None])[0]}
                          for r in kids[:12]],
            "more_investees": max(0, len(kids) - 12),
            "sources": _receipts(top + kids[:12]),
            "notes": ["주주는 최대주주와 특수관계인, 그리고 지분을 자기 보고서에 적은 회사만입니다. 5% 이상 주주 전체가 아닙니다.",
                      "지분율은 가장 최근 사업보고서의 표에 적힌 값입니다."],
            "summary": f"주주 {len(rows)}곳(위 {len(top)}곳 표시), 출자 대상 {len(kids)}곳(위 {min(12, len(kids))}곳 표시)"}


def group(db, tools, company: dict, when: str) -> dict:
    members = tools.get_relations(db, company["company_id"], when, "affiliate", "out")
    rows = members["relations"]
    codes = {}
    people = []
    for r in rows:
        other = r["object"] if r["subject"].get("company_id") == company["company_id"] else r["subject"]
        listed = r["detail"].get("listed") == "상장" or other.get("market") in ("유가증권", "코스닥", "코넥스")
        people.append({**_who(other), "listed": listed, "stock_code": other.get("stock_code")})
        if other.get("stock_code"):
            codes[other["stock_code"]] = None
    if company.get("stock_code"):
        people.insert(0, {"id": company["company_id"], "name": company["name"], "listed": True, "stock_code": company["stock_code"], "self": True})
        codes[company["stock_code"]] = None
    if codes:
        day = db.scalar(select(func.max(PriceDaily.trade_date)).where(PriceDaily.trade_date <= date.fromisoformat(when)))
        if day:
            for code, cap in db.execute(select(PriceDaily.stock_code, PriceDaily.market_cap).where(PriceDaily.trade_date == day, PriceDaily.stock_code.in_(list(codes)))):
                codes[code] = cap
    for person in people:
        person["market_cap"] = codes.get(person.pop("stock_code", None) or "")
    listed = [p for p in people if p["listed"]]
    return {"group": company.get("group"), "members": people,
            "sources": _receipts(rows[:1]),
            "notes": ["회사가 사업보고서에 적은 계열회사 표 기준입니다. 상장사 칸의 크기는 시가총액입니다(한국거래소 통계정보 가공)."],
            "summary": f"계열회사 {len(people)}곳 가운데 상장 {len(listed)}곳"}


def supply_flow(db, tools, company: dict, when: str, start: str) -> dict:
    flows: dict[tuple, dict] = {}
    used = []
    for direction in ("out", "in"):
        found = tools.get_relations(db, company["company_id"], when, "supply_contract", direction, disclosed_from=start)
        for r in found["relations"]:
            seller, buyer = _who(r["subject"]), _who(r["object"])
            if r["detail"].get("party_hidden"):
                buyer = {"id": None, "name": "비공개 상대", "listed": False}
            key = (seller["name"], buyer["name"])
            flow = flows.setdefault(key, {"from": seller, "to": buyer, "amount": 0, "count": 0})
            flow["amount"] += r["value"] or 0
            flow["count"] += 1
            used.append(r)
    ranked = sorted(flows.values(), key=lambda f: -f["amount"])
    return {"period": {"from": start, "to": when}, "flows": ranked[:15], "more": max(0, len(ranked) - 15),
            "sources": _receipts(used)[:40],
            "notes": ["건별로 공시한 공급계약만입니다. 계약금액의 합이며 실제 매출과 다릅니다. 정정 뒤의 값이고 해지된 계약은 빠집니다."],
            "summary": f"{start} 이후 공급계약 {len(used)}건, 상대 {len(ranked)}쌍"}


def path(db, tools, company: dict, other_id: int, when: str) -> dict:
    found = tools.find_paths(db, company["company_id"], other_id, when)
    routes = []
    for route in found["paths"][:3]:
        steps = [{"from": _who(e["subject"]), "to": _who(e["object"]), "type": e["type"], "value": e["value"], "unit": e["unit"],
                  "rcept": (e["rcept_no"] or [None])[0]} for e in route]
        routes.append(steps)
    return {"to": _who(found["to"]), "routes": routes, "total": found["total"],
            "sources": _receipts(e for route in found["paths"][:3] for e in route),
            "notes": ["공시로 확인되는 관계만 잇습니다. 세 단계까지 찾습니다."],
            "summary": f"경로 {found['total']}개" + ("" if found["total"] else " (세 단계 안에 이어지지 않음)")}


def timeline(db, tools, company: dict, when: str, start: str) -> dict:
    events = []
    for doc_type in ("supply_contract", "event"):
        found = tools.get_filings(db, company["company_id"], when, doc_type, filed_from=start)
        for f in found.get("filings", []):
            first = (f.get("contents") or [{}])[0]
            report = f["report"]
            category = ("정정" if f["is_correction"] else "해지" if "해지" in report else "지분 처분" if "처분" in report
                        else "지분 취득" if "취득" in report or "양수" in report else "공급계약")
            events.append({"date": f["filed"], "category": category, "report": report, "counterparty": first.get("counterparty"),
                           "title": first.get("title"), "amount": first.get("value") if first.get("unit") == "krw" else None,
                           "superseded": f.get("superseded_by_later_filing"), "rcept": f["rcept_no"]})
    events.sort(key=lambda e: e["date"])
    return {"period": {"from": start, "to": when}, "events": events[-60:],
            "sources": [e["rcept"] for e in events[-60:]],
            "notes": ["공급계약과 지분 취득·처분 결정 공시만입니다(사업보고서 같은 정기 공시는 빠짐). 원의 크기는 계약·거래 금액입니다."],
            "summary": f"{start} 이후 공시 {len(events)}건"}


def contracts(db, tools, company: dict, when: str, start: str) -> dict:
    bars = []
    for direction, side in (("out", "판매"), ("in", "구매")):
        found = tools.get_relations(db, company["company_id"], when, "supply_contract", direction, disclosed_from=start)
        for r in found["relations"]:
            d = r["detail"]
            if not d.get("period_start") or not d.get("period_end"):
                continue
            other = r["object"] if direction == "out" else r["subject"]
            bars.append({"side": side, "counterparty": "비공개 상대" if d.get("party_hidden") else other.get("name"),
                         "counterparty_id": None if d.get("party_hidden") else other.get("company_id"),
                         "title": d.get("title"), "start": d["period_start"], "end": d["period_end"], "amount": r["value"],
                         "ratio_pct": d.get("ratio_pct"), "rcept": (r["rcept_no"] or [None])[0]})
    bars.sort(key=lambda b: -(b["amount"] or 0))
    shown = sorted(bars[:15], key=lambda b: b["start"])
    return {"period": {"from": start, "to": when}, "bars": shown, "more": max(0, len(bars) - 15),
            "sources": [b["rcept"] for b in shown],
            "notes": ["계약기간이 적힌 공급계약만입니다. 금액이 큰 15건을 보입니다."],
            "summary": f"계약기간이 있는 계약 {len(bars)}건"}


def revenue(db, tools, company: dict, when: str) -> dict:
    found = tools.get_products(db, company["company_id"], when)
    rows = [{"name": p["name"], "segment": p.get("segment"), "share_pct": p.get("share_pct"),
             "family": ((p.get("products") or [{}])[0]).get("family")} for p in found.get("products", [])]
    known = sum(r["share_pct"] or 0 for r in rows)
    return {"report": found.get("report"), "rows": rows,
            "sources": [found["rcept_no"]] if found.get("rcept_no") else [],
            "notes": [n for n in ("보고서의 제품별 매출 표에 적힌 비중입니다." ,
                                  "비중이 적히지 않은 줄이 있습니다." if any(r["share_pct"] is None for r in rows) else "",
                                  "읽은 표가 없습니다." if not found.get("read") else "") if n],
            "summary": f"{found.get('report')}: 제품 {len(rows)}줄, 비중 합 {round(known, 1)}%" if rows else "제품 표를 읽지 못함"}


def returns(db, tools, company: dict, when: str) -> dict:
    found = tools.get_price_history(db, company["company_id"], when)
    return {"last_trading_day": found.get("last_trading_day"), "periods": found.get("periods", []), "peers": found.get("peers"),
            "sources": [],
            "notes": ["종가 기준 수익률입니다. 장중 값이 아니며 원인을 말해 주지 않습니다. 한국거래소(KRX) 통계정보를 가공했습니다."],
            "summary": "; ".join(f"{p['period']} {p.get('change_pct')}% (시장 {p.get('market_median_pct')}%)" for p in found.get("periods", []) if "change_pct" in p)}


def _fin_sources(found: dict) -> list[str]:
    out = []
    for item in found.get("quarters", []) + [found["balance"]["latest"]]:
        for no in item.get("rcept_no") or []:
            if no not in out:
                out.append(no)
    return out


def _basis_note(found: dict) -> str:
    return f"{found['basis_name']} 재무제표 기준, 금액 단위 {found['unit']}. 정기보고서의 주요계정입니다."


def financials(db, tools, company: dict, when: str) -> dict:
    found = tools.financial_data(db, company["company_id"], when)
    rows = found["quarters"]
    notes = [_basis_note(found)]
    if any(q.get("computed") for q in rows):
        notes.append("4분기는 공시되지 않아 연간 값에서 3분기 누적을 빼서 계산했습니다.")
    if any(q.get("not_computed") for q in rows):
        notes.append("분기 값이 나중에 다시 작성된 해는 4분기를 계산하지 않았습니다.")
    last = next((q for q in reversed(rows) if q.get("revenue") is not None or q.get("operating_income") is not None), None)
    summary = ""
    if last:
        if "operating_income_yoy_pct" in last:
            change = f", 영업이익 전년 동기 대비 {last['operating_income_yoy_pct']:+.1f}%"
        elif "operating_income_yoy" in last:
            change = f", 영업이익 {last['operating_income_yoy']}"
        else:
            change = ""
        summary = f"{last['period']} 매출 {readable(last.get('revenue'), found['unit'])}, 영업이익 {readable(last.get('operating_income'), found['unit'])}{change}"
    return {"unit": found["unit"], "basis": found["basis_name"], "quarters": rows, "years": found["years"],
            "sources": _fin_sources(found), "notes": notes, "summary": summary}


def health(db, tools, company: dict, when: str) -> dict:
    found = tools.financial_data(db, company["company_id"], when)
    latest, median = found["balance"]["latest"], found["market_median"]
    year = next((y for y in reversed(found["years"]) if y.get("operating_margin_pct") is not None), {})
    measures = [
        {"key": "debt_ratio", "label": "부채비율", "value": latest.get("debt_ratio_pct"), "median": median.get("debt_ratio_pct"),
         "better": "lower", "how": "부채총계 / 자본총계"},
        {"key": "current_ratio", "label": "유동비율", "value": latest.get("current_ratio_pct"), "median": median.get("current_ratio_pct"),
         "better": "higher", "how": "유동자산 / 유동부채"},
        {"key": "operating_margin", "label": "영업이익률", "value": year.get("operating_margin_pct"), "median": median.get("operating_margin_pct"),
         "better": "higher", "how": f"영업이익 / 매출 ({year.get('period', '')})"},
    ]
    notes = [_basis_note(found),
             f"중앙값은 {median['market']} 상장사 {median['companies']}곳의 가장 최근 보고서 기준입니다. 좋고 나쁨의 기준이 아니라 견주어 보기 위한 값입니다."]
    if latest.get("capital_impaired"):
        notes.append(latest["capital_impaired"])
    return {"unit": found["unit"], "basis": found["basis_name"], "date": latest.get("date"), "measures": measures,
            "balance": latest, "previous": found["balance"].get("previous_year_end"), "market": median["market"],
            "sources": _fin_sources(found), "notes": notes,
            "summary": ", ".join(f"{m['label']} {m['value']}% (시장 중앙값 {m['median']}%)" for m in measures if m["value"] is not None)}


def compare(db, tools, company: dict, other_id: int, when: str) -> dict:
    sides = [tools.financial_data(db, company_id, when) for company_id in (company["company_id"], other_id)]
    # 두 회사가 모두 가진 가장 최근 분기에 맞춘다
    # 결산월이 달라도 같은 달에 끝난 분기끼리 견준다
    ends = [{q["end"]: q for q in s["quarters"] if q.get("end") and (q.get("revenue") is not None or q.get("operating_income") is not None)} for s in sides]
    common = sorted(set(ends[0]) & set(ends[1]))
    period = ends[0][common[-1]]["period"] if common else None
    rows = []
    for found in sides:
        quarter = next((q for q in found["quarters"] if common and q.get("end") == common[-1]), {})
        latest = found["balance"]["latest"]
        rows.append({"id": found["company"]["company_id"], "name": found["company"]["name"], "basis": found["basis_name"], "unit": found["unit"],
                     "revenue": quarter.get("revenue"), "operating_income": quarter.get("operating_income"),
                     "net_income": quarter.get("net_income"), "operating_margin_pct": quarter.get("operating_margin_pct"),
                     "revenue_yoy_pct": quarter.get("revenue_yoy_pct"), "operating_income_yoy_pct": quarter.get("operating_income_yoy_pct"),
                     "debt_ratio_pct": latest.get("debt_ratio_pct"), "balance_date": latest.get("date"),
                     "trend": [{"period": q["period"], "revenue": q.get("revenue"), "operating_income": q.get("operating_income")}
                               for q in found["quarters"] if not q.get("not_computed")][-4:]})
    notes = [f"{period} 석 달 값입니다. 부채비율은 각 회사의 가장 최근 기말 값입니다." if period else "두 회사가 함께 가진 분기가 없습니다."]
    if len({r["basis"] for r in rows}) > 1:
        notes.append("두 회사의 재무제표 기준(연결·별도)이 다릅니다.")
    if len({r["unit"] for r in rows}) > 1:
        notes.append("두 회사의 통화가 달라 금액을 바로 견줄 수 없습니다.")
    sources = []
    for found in sides:
        sources += [n for n in _fin_sources(found) if n not in sources]
    return {"period": period, "rows": rows, "to": {"id": rows[1]["id"], "name": rows[1]["name"]},
            "sources": sources, "notes": notes,
            "summary": "; ".join(f"{r['name']} 매출 {readable(r['revenue'], r['unit'])}, 영업이익률 {r['operating_margin_pct']}%" for r in rows)}


def holders(db, tools, company: dict, when: str) -> dict:
    """최대주주와 특수관계인(사업보고서), 그 밖의 5% 보고자, 나머지를 한 막대에. 5% 보고자가 최대주주 쪽이면 겹치므로 따로 표시한다."""
    table = tools.get_relations(db, company["company_id"], when, "equity", "in")
    found = tools.get_major_holders(db, company["company_id"], when)
    total = table.get("holder_table_total") or {}
    group_pct = float(total["pct"]) if total.get("pct") not in (None, "") else None
    from .names import normalize
    names = {(r.get("subject") or {}).get("name") or r["detail"].get("holder_name_raw") for r in table["relations"]}
    names |= {r["detail"].get("holder_name_raw") for r in table["relations"] if r["detail"].get("holder_name_raw")}
    names = {normalize(n) for n in names if n}
    group = company.get("group")

    def overlaps(h: dict) -> bool:
        """최대주주 쪽이 낸 보고인가: 이름이 같거나(띄어쓰기 무시) 한쪽이 다른 쪽으로 시작하거나, 보고자가 같은 기업집단이다."""
        mine = {normalize(h["reporter"]), normalize((h.get("reporter_company") or {}).get("name") or "")} - {""}
        if any(m == n or (min(len(m), len(n)) >= 2 and (m.startswith(n) or n.startswith(m))) for m in mine for n in names):
            return True
        return bool(group) and (h.get("reporter_company") or {}).get("group") == group
    rows = []
    for h in found["holders"]:
        if h["status"] != "5% 이상":
            continue
        rows.append({"name": h["reporter"], "id": (h.get("reporter_company") or {}).get("company_id"), "pct": h["pct"],
                     "date": h["date"], "overlap": overlaps(h)})
    outside = sum(r["pct"] for r in rows if not r["overlap"] and r["pct"])
    stack = group_pct is not None and group_pct + outside <= 100.5
    rest = round(max(0.0, 100 - group_pct - outside), 2) if stack else None
    notes = ["최대주주와 특수관계인 합계는 가장 최근 사업보고서의 최대주주 현황, 5% 보고자는 각자의 가장 최근 대량보유 보고입니다."]
    if stack:
        notes.append("최대주주 쪽이 낸 5% 보고는 위의 합계와 겹치므로 막대에 더하지 않았습니다. 나머지는 100%에서 뺀 어림값이고 소액주주와 5% 미만 주주가 섞여 있습니다.")
    elif group_pct is not None:
        notes.append("더하면 100%를 넘습니다. 서로 특별관계인 보고자가 각자 특별관계자를 합친 비율로 보고했거나, 보고 시점이 서로 달라서입니다. 그래서 한 막대로 합치지 않았습니다.")
    sources = [n for h in found["holders"] for n in h["rcept_no"]][:20]
    sources += [n for r in table["relations"][:5] for n in (r.get("rcept_no") or [])]
    return {"stack": stack, "group_pct": group_pct, "group_date": table["relations"][0].get("as_of_date") if table["relations"] else None,
            "holders": rows, "rest_pct": rest, "sources": list(dict.fromkeys(sources)), "notes": notes,
            "summary": (f"최대주주와 특수관계인 {group_pct}%, " if group_pct is not None else "")
                       + ", ".join(f"{r['name']} {r['pct']}%" for r in rows if not r["overlap"]) or "5% 보고자 없음"}


def insiders(db, tools, company: dict, when: str, start: str) -> dict:
    """기간 동안 임원·주요주주가 늘리고 줄인 주식 수(보고 접수일별)와, 같은 기간의 주가 수익률(가공값)."""
    from datetime import date as _date_type
    found = tools.get_insider_trades(db, company["company_id"], when, since=start if start else None)
    stock = company.get("stock_code")
    returns = []
    if stock:
        rows = list(db.execute(select(PriceDaily.trade_date, PriceDaily.close_price).where(
            PriceDaily.stock_code == stock, PriceDaily.trade_date >= _date_type.fromisoformat(found["from"]),
            PriceDaily.trade_date <= _date_type.fromisoformat(found["to"])).order_by(PriceDaily.trade_date)))
        if rows:
            base = float(rows[0][1])
            returns = [{"date": d.isoformat(), "pct": round((float(c) / base - 1) * 100, 2)} for d, c in rows]
    t = found["totals"]
    notes = ["날짜는 실제 거래일이 아니라 보고 접수일입니다. 거래 단가와 금액, 장내·장외, 증여·상속 같은 사유는 이 보고 자료에 없습니다."]
    if returns:
        notes.append(f"선은 {returns[0]['date']} 종가를 0으로 둔 주가 수익률입니다(한국거래소 통계정보를 가공). 매매의 원인을 말해 주지 않습니다.")
    else:
        notes.append("이 기간의 시세를 보관하고 있지 않아 수익률 선은 없습니다.")
    return {"from": found["from"], "to": found["to"], "totals": t, "by_day": found["by_day"], "returns": returns,
            "reporters": found["reporters"][:8], "sources": [n for p in found["reporters"][:8] for n in p["rcept_no"]][:20], "notes": notes,
            "summary": f"{found['from']}~{found['to']} 늘린 보고 {t['increased_reports']}건 {t['increased_shares']:,}주, "
                       f"줄인 보고 {t['decreased_reports']}건 {t['decreased_shares']:,}주, 순증감 {t['net_shares']:+,}주 ({t['reporters']}명)"}


def dilution(db, tools, company: dict, when: str, start: str | None) -> dict:
    """주식 수를 늘리거나 줄이는 결정(증자, 사채, 감자)을 시간 축에. 사채는 전환·행사되면 생길 수 있는 주식 수다."""
    from datetime import date as _date_type
    from . import capital
    begin = _date_type.fromisoformat(start) if start else capital.SINCE
    rows = capital.events(db, company["company_id"], begin, _date_type.fromisoformat(when), list(capital.DILUTIVE))
    items = [{"date": r.report_date.isoformat(), "kind": r.kind, "label": capital.LABELS[r.kind], "cancel": capital.is_cancel(r), "shares": r.shares,
              "amount_eok": round(r.amount / 1e8, 1) if r.amount else None, "ratio_pct": float(r.ratio_pct) if r.ratio_pct is not None else None,
              "pct_of_before": round(r.shares / r.shares_before * 100, 2) if r.shares and r.shares_before else None,
              "method": r.method, "price": float(r.price) if r.price is not None else None, "rcept_no": r.rcept_no} for r in rows]
    counts = {}
    for item in items:
        counts[item["label"]] = counts.get(item["label"], 0) + 1
    return {"from": begin.isoformat(), "events": items, "sources": [i["rcept_no"] for i in items][-20:],
            "notes": ["결정 공시입니다. 실제 납입·발행, 사채의 실제 전환, 철회 여부는 이 자료에 없습니다.",
                      "사채의 주식 수는 전환·행사·교환되면 생길 수 있는 주식 수입니다. 감자 가운데 자기주식 소각은 따로 표시했습니다."],
            "summary": ", ".join(f"{k} {v}건" for k, v in counts.items()) or "결정 없음"}


def payout(db, tools, company: dict, when: str) -> dict:
    """사업연도마다 배당총액과 자기주식 취득 결정 금액, 주당배당금과 배당성향."""
    from datetime import date as _date_type
    from . import capital
    years = capital.dividends(db, company["company_id"], _date_type.fromisoformat(when))
    buys = {}
    for row in capital.events(db, company["company_id"], capital.SINCE, _date_type.fromisoformat(when), ["buyback", "buyback_trust"]):
        buys[row.report_date.year] = buys.get(row.report_date.year, 0) + (row.amount or 0)
    rows = [{"year": r.fiscal_year, "cash_eok": round(r.cash_total / 1e8, 1) if r.cash_total else 0,
             "buyback_eok": round(buys.get(r.fiscal_year, 0) / 1e8, 1),
             "dps": float(r.dps_common) if r.dps_common is not None else None,
             "payout_ratio": float(r.payout_ratio) if r.payout_ratio is not None else None} for r in years]
    for year in sorted(set(buys) - {r["year"] for r in rows}):
        rows.append({"year": year, "cash_eok": None, "buyback_eok": round(buys[year] / 1e8, 1), "dps": None, "payout_ratio": None})
    rows.sort(key=lambda r: r["year"])
    sources = list(dict.fromkeys([r.rcept_no for r in years]))
    return {"years": rows, "sources": sources, "notes": [
        "배당은 사업보고서의 배당에 관한 사항(2025 사업보고서의 당기·전기·전전기)입니다.",
        "자기주식은 그해 공시한 취득 결정과 신탁계약의 예정 금액이고 실제 취득액이 아닙니다(2024-01 부터 모음). 소각은 감자결정으로 공시한 것만 셉니다."],
        "summary": "; ".join(f"{r['year']} 주당 {r['dps']:,.0f}원" + (f", 배당성향 {r['payout_ratio']}%" if r["payout_ratio"] is not None else "") for r in rows if r["dps"])
                   or ("현금배당 없음, 자기주식 취득 결정만" if buys else "배당 기록 없음")}


def build(db, tools, kind: str, company_id: int, as_of: str, other_company_id: int | None = None, since: str | None = None) -> dict:
    """보기 하나를 만든다. tools 는 agent_tools 모듈(서로 가져오면 순환하므로 넘겨받는다)."""
    company = tools._brief(tools._company(db, company_id))
    when = tools._date(as_of, "as_of", required=True)
    start = (tools._date(since, "since") or (when - timedelta(days=365))).isoformat()
    as_of = when.isoformat()
    if kind in ("path", "compare"):
        if other_company_id is None:
            raise tools.ToolError(f"{kind} 는 other_company_id 가 필요합니다")
        body = (path if kind == "path" else compare)(db, tools, company, other_company_id, as_of)
    elif kind == "insiders":
        body = insiders(db, tools, company, as_of, since)
    elif kind == "dilution":
        body = dilution(db, tools, company, as_of, since)
    elif kind in ("supply_flow", "timeline", "contracts"):
        body = {"supply_flow": supply_flow, "timeline": timeline, "contracts": contracts}[kind](db, tools, company, as_of, start)
    elif kind in KINDS:
        body = {"ownership": ownership, "group": group, "revenue": revenue, "returns": returns,
                "financials": financials, "health": health, "holders": holders, "payout": payout}[kind](db, tools, company, as_of)
    else:
        raise tools.ToolError(f"kind 는 {', '.join(KINDS)} 가운데 하나입니다")
    title = f"{company['name']} ↔ {body['to']['name']} {KINDS[kind]}" if kind in ("path", "compare") else f"{company['name']} {KINDS[kind]}"
    return {"kind": kind, "title": title, "company": {"id": company["company_id"], "name": company["name"]},
            "as_of": as_of, **body}

