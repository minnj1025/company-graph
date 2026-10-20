"""자본 변동 결정(증자, 감자, 사채, 자기주식)과 배당을 받아 표에 넣는다.

- capital_event: 주요사항보고서의 결정 공시. OpenDART 주요정보 API 는 결정마다 가장 나중에 정정된 내용을 준다(정정 공시를 따로 지우지 않아도 된다).
  회사마다 모든 종류를 부르면 하루 한도를 넘으므로, 먼저 공시 목록(pblntf_ty=B)에서 그 결정을 낸 회사만 골라 그 종류의 API 만 부른다.
- dividend: 사업보고서의 '배당에 관한 사항'. 2025 사업보고서 하나에 당기·전기·전전기(2025·2024·2023)가 함께 있다.

결정 공시만 있다: 실제 납입·발행 여부, 사채의 실제 전환, 자기주식의 실제 취득 결과, 철회는 알 수 없다. 자기주식 소각 결정은 거래소 공시라 없다.

실행: python -m company_graph.capital load     (2024-01 부터 결정 공시, 그리고 배당)
      python -m company_graph.capital update   (최근 사흘 결정 공시를 낸 회사만)
"""
import re
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select

from .cache import cached_json
from .db import CapitalEvent, Company, Dividend, session

SINCE = date(2024, 1, 1)
# 공시 이름(괄호 안) → (종류, API)
KINDS = {
    "유상증자결정": ("rights_issue", "piicDecsn"),
    "무상증자결정": ("bonus_issue", "fricDecsn"),
    "유무상증자결정": ("rights_bonus_issue", "pifricDecsn"),
    "감자결정": ("capital_reduction", "crDecsn"),
    "전환사채권발행결정": ("cb", "cvbdIsDecsn"),
    "신주인수권부사채권발행결정": ("bw", "bdwtIsDecsn"),
    "교환사채권발행결정": ("eb", "exbdIsDecsn"),
    "자기주식취득결정": ("buyback", "tsstkAqDecsn"),
    "자기주식취득신탁계약체결결정": ("buyback_trust", "tsstkAqTrctrCnsDecsn"),
    "자기주식처분결정": ("treasury_sale", "tsstkDpDecsn"),
}
LABELS = {"rights_issue": "유상증자", "bonus_issue": "무상증자", "rights_bonus_issue": "유무상증자", "capital_reduction": "감자",
          "cb": "전환사채", "bw": "신주인수권부사채", "eb": "교환사채", "buyback": "자기주식 취득",
          "buyback_trust": "자기주식 취득 신탁계약", "treasury_sale": "자기주식 처분"}
DILUTIVE = ("rights_issue", "bonus_issue", "rights_bonus_issue", "capital_reduction", "cb", "bw", "eb")


def _int(text) -> int | None:
    text = str(text or "").replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _num(text) -> float | None:
    text = str(text or "").replace(",", "").strip()
    try:
        return float(text) if text not in ("", "-") else None
    except ValueError:
        return None


def _day(text) -> date | None:
    found = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(text or ""))
    try:
        return date(int(found[1]), int(found[2]), int(found[3])) if found else None
    except ValueError:
        return None


def _sum(item: dict, keys) -> int | None:
    values = [_int(item.get(k)) for k in keys]
    values = [v for v in values if v is not None]
    return sum(values) if values else None


FUNDS = ("fdpp_fclt", "fdpp_bsninh", "fdpp_op", "fdpp_dtrp", "fdpp_ocsa", "fdpp_etc")
FUND_NAMES = {"fdpp_fclt": "시설자금", "fdpp_bsninh": "영업양수자금", "fdpp_op": "운영자금", "fdpp_dtrp": "채무상환자금",
              "fdpp_ocsa": "타법인 증권 취득자금", "fdpp_etc": "기타자금"}


def event_row(company_id: int, kind: str, item: dict) -> dict:
    """API 한 줄을 표의 공통 칸으로 옮긴다. 공통 칸에 없는 값은 attrs 에 둔다('-' 와 빈 값은 뺀다)."""
    get = item.get
    row = {"company_id": company_id, "rcept_no": item["rcept_no"], "kind": kind,
           "report_date": datetime.strptime(item["rcept_no"][:8], "%Y%m%d").date()}
    funds = {FUND_NAMES[k]: _int(get(k)) for k in FUNDS if _int(get(k))}
    if kind in ("rights_issue", "bonus_issue", "rights_bonus_issue"):
        row.update(shares=_sum(item, ("nstk_ostk_cnt", "nstk_estk_cnt")) or _sum(item, ("piic_nstk_ostk_cnt", "piic_nstk_estk_cnt")),
                   shares_before=_sum(item, ("bfic_tisstk_ostk", "bfic_tisstk_estk")) or _sum(item, ("piic_bfic_tisstk_ostk", "piic_bfic_tisstk_estk")),
                   amount=sum(funds.values()) or None, method=get("ic_mthn") or get("piic_ic_mthn"))
    elif kind == "capital_reduction":
        row.update(shares=_sum(item, ("crstk_ostk_cnt", "crstk_estk_cnt")), shares_before=_sum(item, ("bfcr_tisstk_ostk", "bfcr_tisstk_estk")),
                   ratio_pct=_num(get("cr_rt_ostk")), method=get("cr_mth"), purpose=get("cr_rs"))
    elif kind in ("cb", "bw", "eb"):
        row.update(amount=_int(get("bd_fta")), method=get("bdis_mthn"),
                   price=_num(get("cv_prc") or get("ex_prc") or get("nstk_isstk_prc") or get("ex_prc")),
                   shares=_int(get("cvisstk_cnt") or get("nstk_isstk_cnt") or get("extg_stkcnt")),
                   ratio_pct=_num(get("cvisstk_tisstk_vs") or get("nstk_isstk_tisstk_vs") or get("extg_tisstk_vs")),
                   period_start=_day(get("cvrqpd_bgd") or get("expd_bgd") or get("exrqpd_bgd")),
                   period_end=_day(get("cvrqpd_edd") or get("expd_edd") or get("exrqpd_edd")))
    elif kind == "buyback":
        row.update(shares=_sum(item, ("aqpln_stk_ostk", "aqpln_stk_estk")), amount=_sum(item, ("aqpln_prc_ostk", "aqpln_prc_estk")),
                   purpose=get("aq_pp"), method=get("aq_mth"), period_start=_day(get("aqexpd_bgd")), period_end=_day(get("aqexpd_edd")))
    elif kind == "buyback_trust":
        row.update(amount=_int(get("ctr_prc")), purpose=get("ctr_pp"), period_start=_day(get("ctr_pd_bgd")), period_end=_day(get("ctr_pd_edd")))
    elif kind == "treasury_sale":
        row.update(shares=_sum(item, ("dppln_stk_ostk", "dppln_stk_estk")), amount=_sum(item, ("dppln_prc_ostk", "dppln_prc_estk")),
                   price=_num(get("dpstk_prc_ostk")), purpose=get("dp_pp"),
                   period_start=_day(get("dpprpd_bgd")), period_end=_day(get("dpprpd_edd")))
    for key in ("method", "purpose"):
        if row.get(key):
            row[key] = str(row[key]).strip()[:300] or None
    keep = {k: str(v).strip()[:200] for k, v in item.items()
            if k not in ("rcept_no", "corp_cls", "corp_code", "corp_name") and str(v).strip() not in ("", "-")}
    if funds:
        keep["funds"] = funds
    row["attrs"] = keep
    return row


def _filings(start: date, end: date) -> list[dict]:
    """주요사항보고 목록. 회사를 정하지 않으면 석 달씩만 받을 수 있다. 지난 구간은 캐시에 둔다."""
    from . import dart
    out = []
    while start <= end:
        stop = min(start + timedelta(days=89), end)

        def fetch(start=start, stop=stop):
            rows, page = [], 1
            while True:
                body = dart._json("list.json", bgn_de=f"{start:%Y%m%d}", end_de=f"{stop:%Y%m%d}", pblntf_ty="B", page_count=100, page_no=page)
                rows += body.get("list", [])
                if page >= int(body.get("total_page") or 1):
                    return rows
                page += 1
        key = f"{start:%Y%m%d}_{stop:%Y%m%d}"
        out += fetch() if stop >= date.today() - timedelta(days=7) else cached_json("dart_filings_B_market", key, fetch)
        start = stop + timedelta(days=1)
    return out


def corrections(filings: list[dict]) -> set[str]:
    """정정 공시의 접수번호 ([기재정정], [첨부정정] 등)."""
    return {f["rcept_no"] for f in filings if "정정" in f["report_nm"]}


def latest_only(rows: list[dict], corrected: set[str] = frozenset()) -> list[dict]:
    """같은 사채의 원래 공시와 정정 공시가 함께 오면 나중 것만 남긴다. 사채는 회차(bd_tm)가 같으면 같은 사채다.
    다른 종류(증자, 감자, 자기주식)는 API 가 이미 정정된 것 하나만 준다(같은 날 다른 증자 결정이 둘 나오기도 해서 날짜로 묶지 않는다)."""
    rows = sorted(rows, key=lambda r: r["rcept_no"])
    dropped: set[str] = set()
    by_round: dict[str, dict] = {}
    for row in rows:
        number = (row["attrs"] or {}).get("bd_tm")
        if row["kind"] in ("cb", "bw", "eb") and number:
            if number in by_round:
                dropped.add(by_round[number]["rcept_no"])
                row["attrs"]["corrects"] = by_round[number]["rcept_no"]
            by_round[number] = row
    return [r for r in rows if r["rcept_no"] not in dropped]


def wanted(filings: list[dict]) -> dict[str, set[str]]:
    """corp_code → 그 회사가 낸 결정의 공시 이름(괄호 안). 상장사만."""
    out: dict[str, set[str]] = {}
    for f in filings:
        if f.get("corp_cls") not in ("Y", "K", "N"):
            continue
        name = re.sub(r"\[[^\]]*\]", "", f["report_nm"]).strip()
        inner = re.search(r"\(([^()]*)\)\s*$", name)
        if inner and inner[1] in KINDS:
            out.setdefault(f["corp_code"], set()).add(inner[1])
    return out


def store_events(db, company_id: int, corp_code: str, names: set[str], fresh: bool, corrected: set[str] = frozenset()) -> int:
    from . import dart
    count = 0
    for name in sorted(names):
        kind, api = KINDS[name]
        call = lambda: dart._json(f"{api}.json", corp_code=corp_code, bgn_de=f"{SINCE:%Y%m%d}", end_de=f"{date.today():%Y%m%d}").get("list", [])
        items = call() if fresh else cached_json(f"dart_{api}", f"{corp_code}_{date.today():%Y%m%d}", call)
        rows = latest_only(list({i["rcept_no"]: event_row(company_id, kind, i) for i in items if i.get("rcept_no")}.values()), corrected)
        db.execute(delete(CapitalEvent).where(CapitalEvent.company_id == company_id, CapitalEvent.kind == kind))
        if rows:
            db.execute(CapitalEvent.__table__.insert(), rows)
            count += len(rows)
    return count


DIVIDEND_ROWS = {
    ("주당 현금배당금(원)", "보통주"): "dps_common", ("주당 현금배당금(원)", "우선주"): "dps_preferred",
    ("현금배당금총액(백만원)", None): "cash_total", ("(연결)현금배당성향(%)", None): "payout_ratio", ("현금배당성향(%)", None): "payout_ratio",
    ("현금배당수익률(%)", "보통주"): "yield_common", ("(연결)주당순이익(원)", None): "eps", ("주당순이익(원)", None): "eps",
    ("주식배당금총액(백만원)", None): "stock_total",
}


def dividend_rows(company_id: int, items: list[dict]) -> list[dict]:
    """2025 사업보고서 한 건의 표 → 사업연도마다 한 줄 (당기 2025, 전기 2024, 전전기 2023)."""
    if not items:
        return []
    year = int(str(items[0].get("stlm_dt") or "2025")[:4]) if items[0].get("stlm_dt") else 2025
    rows = {y: {"company_id": company_id, "fiscal_year": y, "rcept_no": items[0]["rcept_no"],
                "basis": "CFS" if any(i.get("se", "").startswith("(연결)") for i in items) else "OFS"} for y in (year, year - 1, year - 2)}
    for item in items:
        se = (item.get("se") or "").strip()
        kind = (item.get("stock_knd") or "").strip() or None
        # 주식 종류 칸을 '-' 로 두는 회사(보통주만 있는 경우)와 '종류주'라고 적는 회사가 있다
        kind = {"-": "보통주", "종류주": "우선주"}.get(kind, kind)
        if kind is None and se in ("주당 현금배당금(원)", "현금배당수익률(%)"):
            kind = "보통주"
        column = DIVIDEND_ROWS.get((se, kind)) or DIVIDEND_ROWS.get((se, None))
        if not column:
            continue
        for offset, key in enumerate(("thstrm", "frmtrm", "lwfr")):
            value = _num(item.get(key))
            if value is None or rows[year - offset].get(column) is not None:
                continue
            rows[year - offset][column] = int(value * 1_000_000) if column in ("cash_total", "stock_total") else value
    columns = [c.name for c in Dividend.__table__.columns]
    return [{c: r.get(c) for c in columns} for r in rows.values() if any(r.get(c) is not None for c in ("dps_common", "cash_total", "payout_ratio"))]


def store_dividends(db, company_id: int, corp_code: str, fresh: bool = False) -> int:
    from . import dart
    call = lambda: dart._json("alotMatter.json", corp_code=corp_code, bsns_year="2025", reprt_code="11011").get("list", [])
    items = call() if fresh else cached_json("dart_alot_2025", corp_code, call)
    rows = dividend_rows(company_id, items)
    db.execute(delete(Dividend).where(Dividend.company_id == company_id))
    if rows:
        db.execute(Dividend.__table__.insert(), rows)
    return len(rows)


def listed(db) -> dict[str, int]:
    return dict(db.execute(select(Company.corp_code, Company.company_id).where(
        Company.stock_code.is_not(None), Company.corp_code.is_not(None), Company.corp_cls.in_(("Y", "K", "N")))).all())


def load() -> None:
    from . import dart
    filings = _filings(SINCE, date.today())
    targets, corrected = wanted(filings), corrections(filings)
    print(f"주요사항보고 {len(filings)}건, 결정을 낸 상장사 {len(targets)}곳 (DART 호출 오늘 {dart.calls_today()}건)", flush=True)
    with session() as db:
        ids = listed(db)
        events = 0
        for n, (corp_code, names) in enumerate(sorted(targets.items()), 1):
            if corp_code in ids:
                events += store_events(db, ids[corp_code], corp_code, names, fresh=False, corrected=corrected)
            if n % 100 == 0:
                db.commit()
                print(f"  결정 {n}/{len(targets)}곳, {events}건 (DART 호출 오늘 {dart.calls_today()}건)", flush=True)
        db.commit()
    load_dividends()


def load_dividends() -> None:
    from . import dart
    with session() as db:
        ids = listed(db)
        dividends = 0
        for n, (corp_code, company_id) in enumerate(sorted(ids.items()), 1):
            dividends += store_dividends(db, company_id, corp_code)
            if n % 300 == 0:
                db.commit()
                print(f"  배당 {n}/{len(ids)}곳, {dividends}줄 (DART 호출 오늘 {dart.calls_today()}건)", flush=True)
        db.commit()
    print(f"배당 끝: {dividends}줄", flush=True)


def update(days: int = 3) -> None:
    recent = _filings(date.today() - timedelta(days=days), date.today())
    targets = wanted(recent)
    corrected = corrections(_filings(SINCE, date.today())) if targets else set()
    with session() as db:
        ids = listed(db)
        count = sum(store_events(db, ids[c], c, names, fresh=True, corrected=corrected) for c, names in targets.items() if c in ids)
        db.commit()
    print(f"최근 {days}일 결정을 낸 상장사 {len(targets)}곳, 결정 {count}건을 다시 넣음")


# ---- 조회 ----------------------------------------------------------------------------------------------------

NOT_HERE = ("결정 공시만 있다. 실제 납입·발행 여부, 사채의 실제 전환·행사 주식 수, 자기주식의 실제 취득 결과, 철회는 이 자료에 없다. "
            "자기주식 소각은 감자결정으로 공시한 것만 있다(treasury_cancel). 거래소에만 낸 소각 결정과 2024-01 이전 결정은 없다. "
            "date 는 그 결정의 마지막 정정 공시 접수일일 수 있다(API 가 정정된 내용만 준다).")


def is_cancel(row) -> bool:
    """감자결정이 자기주식 소각인가 (자기주식을 없애는 감자. 주주에게 돌려주는 쪽이지 결손 보전 감자가 아니다)."""
    text = " ".join(filter(None, [row.purpose, row.method, str((row.attrs or {}).get("cr_rs", ""))]))
    return row.kind == "capital_reduction" and "소각" in text and ("자기주식" in text or "자사주" in text or "이익" in text)


def events(db, company_id: int, start: date, end: date, kinds: list[str] | None = None) -> list[CapitalEvent]:
    query = select(CapitalEvent).where(CapitalEvent.company_id == company_id, CapitalEvent.report_date >= start,
                                       CapitalEvent.report_date <= end)
    if kinds:
        query = query.where(CapitalEvent.kind.in_(kinds))
    return list(db.scalars(query.order_by(CapitalEvent.report_date, CapitalEvent.rcept_no)))


def dividends(db, company_id: int, as_of: date) -> list[Dividend]:
    rows = db.scalars(select(Dividend).where(Dividend.company_id == company_id).order_by(Dividend.fiscal_year))
    return [r for r in rows if datetime.strptime(r.rcept_no[:8], "%Y%m%d").date() <= as_of]


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "load"
    if command == "load":
        load()
    elif command == "dividends":
        load_dividends()
    elif command == "update":
        update(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
