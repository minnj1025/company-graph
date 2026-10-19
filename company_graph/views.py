"""Agent가 고르는 '보기'(시각화 카드).

Agent는 보기의 종류와 대상만 고르고, 카드에 들어갈 숫자는 여기서 조회 도구의 결과로 채운다.
그래서 카드의 값은 모델이 옮겨 적은 것이 아니라 DB에서 바로 온 것이다. 화면(web/src/Views.tsx)이 이 모양을 그린다.
"""
from datetime import date, timedelta

from sqlalchemy import func, select

from .db import PriceDaily

KINDS = {
    "ownership": "지분 구조도",
    "group": "계열 지도",
    "supply_flow": "공급망 흐름",
    "path": "연결 경로",
    "timeline": "공시 타임라인",
    "contracts": "계약 기간",
    "revenue": "매출 구성",
    "returns": "수익률 비교",
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


def build(db, tools, kind: str, company_id: int, as_of: str, other_company_id: int | None = None, since: str | None = None) -> dict:
    """보기 하나를 만든다. tools 는 agent_tools 모듈(서로 가져오면 순환하므로 넘겨받는다)."""
    company = tools._brief(tools._company(db, company_id))
    when = tools._date(as_of, "as_of", required=True)
    start = (tools._date(since, "since") or (when - timedelta(days=365))).isoformat()
    as_of = when.isoformat()
    if kind == "path":
        if other_company_id is None:
            raise tools.ToolError("path 는 other_company_id 가 필요합니다")
        body = path(db, tools, company, other_company_id, as_of)
    elif kind in ("supply_flow", "timeline", "contracts"):
        body = {"supply_flow": supply_flow, "timeline": timeline, "contracts": contracts}[kind](db, tools, company, as_of, start)
    elif kind in KINDS:
        body = {"ownership": ownership, "group": group, "revenue": revenue, "returns": returns}[kind](db, tools, company, as_of)
    else:
        raise tools.ToolError(f"kind 는 {', '.join(KINDS)} 가운데 하나입니다")
    title = f"{company['name']} ↔ {body['to']['name']} {KINDS[kind]}" if kind == "path" else f"{company['name']} {KINDS[kind]}"
    return {"kind": kind, "title": title, "company": {"id": company["company_id"], "name": company["name"]},
            "as_of": as_of, **body}

