"""내 종목: 방문자가 고른 보유·관심 종목의 등락률, 비중, 최근 공시, 연관 급등·급락에 들었던 날.

종목 목록은 방문자의 브라우저에만 있다(서버에 저장하지 않는다). 화면이 목록을 보내면 이 함수가 한 번에 정리해 돌려준다.
거래소 시세는 화면에 그대로 내지 않는다. 등락률(%)과, 보유 수량 × 최근 종가로 계산한 비중(%)만 돌려준다.
기사는 따로 찾지 않는다(유료). 그 종목이 연관 급등·급락에 들었던 날 이미 찾아 둔 기사(hot_news.py)만 붙인다.
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import or_, select

from . import query
from .capital import LABELS as CAPITAL_LABELS
from .db import CapitalEvent, Company, Financial, HotDay, InsiderReport, MajorHolding, PriceDaily, Relation
from .financials import readable

DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
PERIODS = {"d1": 1, "d5": 5, "d20": 20}
REPORT_NAMES = {"q1": "1분기보고서", "half": "반기보고서", "q3": "3분기보고서", "annual": "사업보고서"}
DEAL_TYPES = ("supply_contract", "supply_termination", "stake_acquisition", "stake_disposal")


def _won(value) -> str | None:
    return None if not value else readable(float(value) / 1e8)


def _pct(a, b) -> float | None:
    return None if not a or not b else round((float(a) / float(b) - 1) * 100, 2)


def moves(db, companies: dict[int, Company]) -> tuple[dict[int, dict], str | None]:
    """종목마다 1·5·20거래일 등락률과 최근 20거래일의 누적 등락률 띠. 종가는 비중 계산에만 쓰고 돌려주지 않는다."""
    codes = {c.stock_code: c.company_id for c in companies.values() if c.stock_code}
    if not codes:
        return {}, None
    last = db.scalar(select(PriceDaily.trade_date).order_by(PriceDaily.trade_date.desc()).limit(1))
    if last is None:
        return {}, None
    rows = db.execute(select(PriceDaily.stock_code, PriceDaily.trade_date, PriceDaily.close_price, PriceDaily.change_pct)
                      .where(PriceDaily.stock_code.in_(list(codes)), PriceDaily.trade_date >= last - timedelta(days=45))
                      .order_by(PriceDaily.trade_date)).all()
    series: dict[str, list] = defaultdict(list)
    for code, day, close, change in rows:
        series[code].append((day, close, change))
    out = {}
    for code, points in series.items():
        day, close, change = points[-1]
        item = {"day": day.isoformat(), "close": float(close), "stale": day < last,
                "d1": None if change is None else float(change)}
        for key, n in PERIODS.items():
            if key != "d1" and len(points) > n:
                item[key] = _pct(close, points[-1 - n][1])
        base = points[-21][1] if len(points) > 20 else points[0][1]
        item["spark"] = [_pct(p[1], base) or 0.0 for p in points[-21:]]
        out[codes[code]] = item
    return out, last.isoformat()


def events(db, ids: set[int], since: date, until: date) -> list[dict]:
    """최근 공시: 공급계약·해지·지분 결정, 자본 결정, 5% 보고, 임원·주요주주 소유보고(날마다 묶음), 정기보고서."""
    names = {c.company_id: c.label for c in db.scalars(select(Company).where(Company.company_id.in_(ids)))}
    out: list[dict] = []

    def add(day: date, company_id: int, kind: str, text: str, rcept_no: str, tone: str = "info"):
        out.append({"date": day.isoformat(), "company_id": company_id, "company": names.get(company_id), "kind": kind,
                    "text": text, "rcept_no": rcept_no, "url": DART + rcept_no, "tone": tone})

    seen = set()
    for r in db.scalars(select(Relation).where(Relation.rel_type.in_(DEAL_TYPES), Relation.retired_at.is_(None),
                                               Relation.disclosed_date >= since, Relation.disclosed_date <= until,
                                               or_(Relation.subject_company_id.in_(ids), Relation.object_company_id.in_(ids)))):
        mine = r.subject_company_id if r.subject_company_id in ids else r.object_company_id
        if (r.rcept_no, mine) in seen:
            continue
        seen.add((r.rcept_no, mine))
        other_id = r.object_company_id if mine == r.subject_company_id else r.subject_company_id
        other = (db.get(Company, other_id).label if other_id else None) or (r.object_name_raw if mine == r.subject_company_id else None) or "상대 비공개"
        title = (r.attrs or {}).get("title")
        amount = _won(r.value_num) if r.value_unit == "krw" else None
        label = query.LABELS[r.rel_type]
        if r.rel_type == "supply_contract":
            side = "수주" if mine == r.subject_company_id else "발주(상대가 공시)"
            text = f"{label} {side} · {other}" + (f" · '{title}'" if title else "") + (f" · {amount}" if amount else "")
        elif r.rel_type == "supply_termination":
            text = f"{label} · {other}" + (f" · {amount}" if amount else "")
        else:
            actor = "이 회사가" if mine == r.subject_company_id else f"{other}이(가) 이 회사"
            text = f"{label} · {actor} " + (f"{other} " if mine == r.subject_company_id else "") + "지분" + (f" · {amount}" if amount else "")
        add(r.disclosed_date, mine, label, text, r.rcept_no, "deal")
    for e in db.scalars(select(CapitalEvent).where(CapitalEvent.company_id.in_(ids), CapitalEvent.report_date >= since,
                                                   CapitalEvent.report_date <= until)):
        amount = _won(e.amount)
        add(e.report_date, e.company_id, "자본 결정", f"{CAPITAL_LABELS.get(e.kind, e.kind)} 결정" + (f" · {amount}" if amount else ""), e.rcept_no, "capital")
    for h in db.scalars(select(MajorHolding).where(MajorHolding.company_id.in_(ids), MajorHolding.report_date >= since,
                                                   MajorHolding.report_date <= until)):
        pct = "" if h.pct is None else f" {float(h.pct):.2f}%"
        add(h.report_date, h.company_id, "5% 보고", f"대량보유 보고 · {h.reporter}{pct}", h.rcept_no, "holder")
    insiders: dict[tuple, list] = defaultdict(list)
    for i in db.scalars(select(InsiderReport).where(InsiderReport.company_id.in_(ids), InsiderReport.report_date >= since,
                                                    InsiderReport.report_date <= until)):
        insiders[(i.company_id, i.report_date)].append(i)
    for (company_id, day), rows in insiders.items():
        up = sum(1 for i in rows if (i.shares_change or 0) > 0)
        down = sum(1 for i in rows if (i.shares_change or 0) < 0)
        text = f"임원·주요주주 소유보고 {len(rows)}건" + (f" (늘림 {up}, 줄임 {down})" if up or down else "")
        add(day, company_id, "소유보고", text, sorted(rows, key=lambda i: i.rcept_no)[0].rcept_no, "holder")
    reports = set()
    for f in db.scalars(select(Financial).where(Financial.company_id.in_(ids))):
        filed = date(int(f.rcept_no[:4]), int(f.rcept_no[4:6]), int(f.rcept_no[6:8]))
        if since <= filed <= until and (f.company_id, f.rcept_no) not in reports:
            reports.add((f.company_id, f.rcept_no))
            add(filed, f.company_id, "정기보고서", f"{f.fiscal_year} {REPORT_NAMES.get(f.report, f.report)}", f.rcept_no, "report")
    out.sort(key=lambda e: (e["date"], e["rcept_no"]), reverse=True)
    return out


def issues(db, ids: set[int], since: date) -> list[dict]:
    """연관 급등·급락이나 개별 급등·급락에 든 날. 그날 찾아 둔 기사가 있으면 함께."""
    out = []
    for row in db.scalars(select(HotDay).where(HotDay.trade_date >= since).order_by(HotDay.trade_date.desc())):
        payload = row.payload
        for side, part in (("up", payload), ("down", payload.get("down") or {})):
            for index, group in enumerate(part.get("groups", [])):
                for member in group.get("members", []):
                    if member["id"] in ids:
                        news = group.get("news") or {}
                        out.append({"day": row.trade_date.isoformat(), "company_id": member["id"], "company": member["name"], "side": side,
                                    "change": member["change"], "group": index, "together": [m["name"] for m in group["members"] if m["id"] != member["id"]][:6],
                                    "why": group.get("why", []), "grade": group.get("grade"),
                                    "reason": news.get("reason") if news.get("found") in ("group", "single") else None,
                                    "sources": (news.get("sources") or [])[:2]})
            for member in part.get("alone", []):
                if member["id"] in ids:
                    out.append({"day": row.trade_date.isoformat(), "company_id": member["id"], "company": member["name"], "side": side,
                                "change": member["change"], "group": None, "together": [], "why": [], "grade": None,
                                "reason": None, "sources": []})
    return out


def summary(db, items: list[tuple[int, float | None]], days: int, today: date) -> dict:
    ids = {company_id for company_id, _ in items}
    companies = {c.company_id: c for c in db.scalars(select(Company).where(Company.company_id.in_(ids)))}
    price, last_day = moves(db, companies)
    values = {company_id: qty * price[company_id]["close"] for company_id, qty in items
              if qty and company_id in price}
    total = sum(values.values())
    stocks = []
    for company_id, qty in items:
        company = companies.get(company_id)
        if company is None:
            continue
        move = {k: v for k, v in (price.get(company_id) or {}).items() if k != "close"}
        stocks.append({"id": company_id, "name": company.label, "stock_code": company.stock_code,
                       "market": {"Y": "코스피", "K": "코스닥"}.get(company.corp_cls), "listed": company.corp_cls in ("Y", "K"),
                       "weight": round(values[company_id] / total * 100, 2) if company_id in values and total else None,
                       "move": move or None})
    since = today - timedelta(days=days)
    return {"as_of": today.isoformat(), "price_day": last_day, "since": since.isoformat(), "stocks": stocks,
            "events": events(db, ids, since, today)[:120], "issues": issues(db, ids, since)[:60],
            "note": "등락률은 거래일 다음 날 나오는 종가 기준이다. 비중은 적은 수량 × 최근 종가로 계산했고 금액은 보여 주지 않는다."}
