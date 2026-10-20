"""지분공시를 받아 표에 넣는다: 5% 대량보유 상황보고(major_holding)와 임원·주요주주 소유보고(insider_report).

OpenDART 는 두 가지 모두 회사 하나씩 부르고, 부르면 그 회사의 보고 전부(최근 약 2년, 2024-10 부터)를 준다.
- 처음: 상장사 전부를 한 번씩 부른다 (2,700여 곳 × 2 = 5,500번 남짓, 하루 한도 20,000번 안)
- 매일: 공시 목록에서 지분공시(pblntf_ty=D)를 낸 회사만 다시 부른다

실행: python -m company_graph.holdings load            (상장사 전부. 받은 것은 캐시에 둔다)
      python -m company_graph.holdings update [날 수]  (최근 며칠 지분공시를 낸 회사만, 기본 3일)
      python -m company_graph.holdings check           (5% 보고와 최대주주 현황을 견준다)
"""
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select

from .cache import cached_json
from .db import Company, InsiderReport, MajorHolding, session


def _int(text) -> int | None:
    text = str(text or "").replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _pct(text) -> Decimal | None:
    text = str(text or "").replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _day(text: str, rcept_no: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return datetime.strptime(rcept_no[:8], "%Y%m%d").date()


def fetch(corp_code: str, kind: str, fresh: bool = False) -> list[dict]:
    from . import dart   # 수집할 때만. 배포된 서버에는 requests 가 없다
    path = {"major": "majorstock.json", "insider": "elestock.json"}[kind]
    call = lambda: dart._json(path, corp_code=corp_code).get("list", [])
    return call() if fresh else cached_json(f"dart_{kind}", f"{corp_code}_{date.today():%Y%m%d}", call)


def major_rows(company_id: int, items: list[dict], index) -> list[dict]:
    from .extract_equity import link
    out = {}
    for item in items:
        out[item["rcept_no"]] = {
            "company_id": company_id, "rcept_no": item["rcept_no"], "report_date": _day(item.get("rcept_dt"), item["rcept_no"]),
            "reporter": (item.get("repror") or "").strip()[:200], "reporter_company_id": link(index, item.get("repror") or ""),
            "report_type": (item.get("report_tp") or "")[:20] or None,
            "shares": _int(item.get("stkqy")), "shares_change": _int(item.get("stkqy_irds")),
            "pct": _pct(item.get("stkrt")), "pct_change": _pct(item.get("stkrt_irds")),
            "contract_shares": _int(item.get("ctr_stkqy")), "reason": (item.get("report_resn") or "").strip()[:500] or None}
    return list(out.values())


def insider_rows(company_id: int, items: list[dict]) -> list[dict]:
    out = {}
    for item in items:
        reporter = (item.get("repror") or "").strip()[:100]
        out[(item["rcept_no"], reporter)] = {
            "company_id": company_id, "rcept_no": item["rcept_no"], "reporter": reporter,
            "report_date": _day(item.get("rcept_dt"), item["rcept_no"]),
            "registered": (item.get("isu_exctv_rgist_at") or "").strip()[:10] or None,
            "position": (item.get("isu_exctv_ofcps") or "").strip()[:60] or None,
            "major_holder": (item.get("isu_main_shrholdr") or "").strip().strip("-")[:40] or None,
            "shares": _int(item.get("sp_stock_lmp_cnt")), "shares_change": _int(item.get("sp_stock_lmp_irds_cnt")),
            "pct": _pct(item.get("sp_stock_lmp_rate")), "pct_change": _pct(item.get("sp_stock_lmp_irds_rate"))}
    return list(out.values())


def store(db, company_id: int, corp_code: str, index, fresh: bool = False) -> tuple[int, int]:
    major = major_rows(company_id, fetch(corp_code, "major", fresh), index)
    insider = insider_rows(company_id, fetch(corp_code, "insider", fresh))
    db.execute(delete(MajorHolding).where(MajorHolding.company_id == company_id))
    db.execute(delete(InsiderReport).where(InsiderReport.company_id == company_id))
    if major:
        db.execute(MajorHolding.__table__.insert(), major)
    if insider:
        db.execute(InsiderReport.__table__.insert(), insider)
    return len(major), len(insider)


def listed(db) -> list[tuple[int, str]]:
    return list(db.execute(select(Company.company_id, Company.corp_code).where(
        Company.stock_code.is_not(None), Company.corp_code.is_not(None), Company.corp_cls.in_(("Y", "K", "N"))).order_by(Company.company_id)))


def load() -> None:
    from . import dart
    from .extract_equity import alias_index
    with session() as db:
        index = alias_index(db)
        companies = listed(db)
        totals = [0, 0]
        for n, (company_id, corp_code) in enumerate(companies, 1):
            major, insider = store(db, company_id, corp_code, index)
            totals[0] += major
            totals[1] += insider
            if n % 100 == 0:
                db.commit()
                print(f"{n}/{len(companies)}곳: 5% 보고 {totals[0]}건, 임원·주요주주 보고 {totals[1]}건 (DART 호출 오늘 {dart.calls_today()}건)", flush=True)
        db.commit()
        print(f"끝: {len(companies)}곳, 5% 보고 {totals[0]}건, 임원·주요주주 보고 {totals[1]}건", flush=True)


def update(days: int = 3) -> None:
    """최근 며칠 동안 지분공시를 낸 회사만 다시 받는다."""
    from . import dart
    from .extract_equity import alias_index
    end = date.today()
    start = end - timedelta(days=days)
    filings = dart._json("list.json", bgn_de=f"{start:%Y%m%d}", end_de=f"{end:%Y%m%d}", pblntf_ty="D", page_count=100)
    codes, page = {f["corp_code"] for f in filings.get("list", [])}, 1
    while page < int(filings.get("total_page") or 1):
        page += 1
        filings = dart._json("list.json", bgn_de=f"{start:%Y%m%d}", end_de=f"{end:%Y%m%d}", pblntf_ty="D", page_count=100, page_no=page)
        codes |= {f["corp_code"] for f in filings.get("list", [])}
    with session() as db:
        index = alias_index(db)
        targets = [(company_id, corp_code) for company_id, corp_code in listed(db) if corp_code in codes]
        for company_id, corp_code in targets:
            store(db, company_id, corp_code, index, fresh=True)
        db.commit()
    print(f"지분공시를 낸 회사 {len(codes)}곳 중 상장사 {len(targets)}곳을 다시 받음")


# ---- 조회 ----------------------------------------------------------------------------------------------------

COVERED_FROM = date(2024, 10, 1)   # OpenDART 가 주는 지분공시는 이때부터다
OVERLAP_NOTE = ("5% 보고의 비율은 대표보고자와 특별관계자를 합친 값이다. 서로 특별관계인 보고자(예: 최대주주와 그 계열사)가 따로 보고했으면 "
                "비율을 더하면 겹칠 수 있다. 최대주주와 특수관계인의 지분 합계는 사업보고서의 최대주주 현황(get_relations equity in)이 따로 있다.")


def major_holders(db, company_id: int, as_of: date, since: date | None = None) -> dict:
    rows = list(db.scalars(select(MajorHolding).where(MajorHolding.company_id == company_id, MajorHolding.report_date <= as_of)
                           .order_by(MajorHolding.report_date, MajorHolding.rcept_no)))
    latest: dict[str, MajorHolding] = {}
    for row in rows:
        latest[row.reporter] = row
    since = since or (as_of - timedelta(days=365))
    holders = sorted(latest.values(), key=lambda r: -(r.pct or 0))
    return {"holders": [_major(r) | {"status": "5% 이상" if (r.pct or 0) >= 5 else "5% 미만으로 내려감"} for r in holders],
            "recent_reports": [_major(r) for r in reversed(rows) if r.report_date >= since][:30],
            "recent_from": since.isoformat(), "total_reports": len(rows)}


def _major(row: MajorHolding) -> dict:
    return {"reporter": row.reporter, "reporter_company_id": row.reporter_company_id, "date": row.report_date.isoformat(),
            "pct": float(row.pct) if row.pct is not None else None,
            "pct_change": float(row.pct_change) if row.pct_change is not None else None,
            "shares": row.shares, "shares_change": row.shares_change, "reason": row.reason,
            **({"new_holder": True} if is_new(row.reason) else {}), "rcept_no": [row.rcept_no]}


NEW = re.compile(r"신규\s*보고|보고\s*의무\s*발생|신규\s*보유|이상\s*신규|신규\s*상장|상장에\s*따른\s*신규")


def is_new(reason: str | None) -> bool:
    """새로 5% 이상이 되어 낸 보고인가. '담보권 신규취득'(주식을 담보로 잡은 것)은 새 보유가 아니다."""
    return bool(reason) and any(NEW.search(line) and "담보" not in line for line in reason.splitlines())


def insider_trades(db, company_id: int, start: date, end: date) -> dict:
    rows = list(db.scalars(select(InsiderReport).where(InsiderReport.company_id == company_id, InsiderReport.report_date >= start,
                                                       InsiderReport.report_date <= end)
                           .order_by(InsiderReport.report_date, InsiderReport.rcept_no)))
    up = [r for r in rows if (r.shares_change or 0) > 0]
    down = [r for r in rows if (r.shares_change or 0) < 0]
    people: dict[str, dict] = {}
    days: dict[str, dict] = {}
    for row in rows:
        person = people.setdefault(row.reporter, {"reporter": row.reporter, "reports": 0, "change": 0, "rcept_no": []})
        person["reports"] += 1
        person["change"] += row.shares_change or 0
        person.update({k: v for k, v in (("position", row.position), ("registered", row.registered), ("major_holder", row.major_holder)) if v})
        person["shares_after"], person["last_date"] = row.shares, row.report_date.isoformat()
        person["pct_after"] = float(row.pct) if row.pct is not None else None
        if len(person["rcept_no"]) < 3:
            person["rcept_no"].append(row.rcept_no)
        day = days.setdefault(row.report_date.isoformat(), {"date": row.report_date.isoformat(), "increased": 0, "decreased": 0, "reports": 0})
        day["reports"] += 1
        if (row.shares_change or 0) > 0:
            day["increased"] += row.shares_change
        elif (row.shares_change or 0) < 0:
            day["decreased"] += -row.shares_change
    ranked = sorted(people.values(), key=lambda p: (-abs(p["change"]), p["reporter"]))
    return {"from": start.isoformat(), "to": end.isoformat(),
            "totals": {"increased_reports": len(up), "increased_shares": sum(r.shares_change for r in up),
                       "decreased_reports": len(down), "decreased_shares": -sum(r.shares_change for r in down),
                       "unchanged_reports": len(rows) - len(up) - len(down),
                       "net_shares": sum(r.shares_change or 0 for r in rows), "reporters": len(people)},
            "reporters": ranked[:25], "reporters_shown": f"{len(ranked)}명 가운데 증감이 큰 {min(25, len(ranked))}명" if len(ranked) > 25 else None,
            "by_day": list(days.values())}


def check(as_of: date | None = None) -> None:
    """5% 보고를 공시 안에서 맞춰 본다.
    1. 비율이 0~100 안에 있나  2. 같은 보고의 주식 증감과 비율 증감의 방향이 같은가
    3. 회사마다 (최대주주와 특수관계인 합계 + 그와 겹치지 않는 5% 보고자) 가 100% 를 넘지 않나 (넘으면 서로 특별관계인 보고자를 두 번 센 것)"""
    import sys as _sys
    from . import agent_tools, views
    as_of = as_of or date.today()
    with session() as db:
        rows = list(db.scalars(select(MajorHolding)))
        bad_range = [r for r in rows if r.pct is not None and not 0 <= r.pct <= 100]
        opposite = [r for r in rows if r.shares_change and r.pct_change and (r.shares_change > 0) != (r.pct_change > 0)]
        print(f"5% 보고 {len(rows)}건: 비율이 0~100 밖 {len(bad_range)}, 주식과 비율의 증감 방향이 반대 {len(opposite)} ({len(opposite) / max(len(rows), 1):.1%})")
        over, checked = [], 0
        for company_id in sorted({r.company_id for r in rows}):
            try:
                view = views.holders(db, agent_tools, agent_tools._brief(db.get(Company, company_id)), as_of.isoformat())
            except Exception:
                continue
            if view["group_pct"] is None:
                continue
            checked += 1
            total = view["group_pct"] + sum(h["pct"] for h in view["holders"] if not h["overlap"])
            if total > 100.5:
                over.append((db.get(Company, company_id).label, round(total, 2)))
        print(f"최대주주 합계가 있는 {checked}곳 중 합이 100%를 넘는 곳 {len(over)}: {over[:8]}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "load"
    if command == "load":
        load()
    elif command == "update":
        update(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif command == "check":
        check()
