"""상장사의 정기보고서 주요계정을 받아 financial 표에 넣는다 (OpenDART 다중회사 주요계정 fnlttMultiAcnt).

한 번 부를 때 100곳까지 받는다. 상장사 2,700여 곳 × 보고서 4종 × 몇 해라도 수백 번이면 끝난다.
- 분기·반기보고서의 손익은 석 달 값(thstrm_amount)과 그해 누적(thstrm_add_amount)이 함께 온다. 사업보고서는 한 해 값 하나다
- 4분기는 공시되지 않는다. 조회할 때 연간 - (1분기 + 2분기 + 3분기)로 계산하고 계산했다고 적는다
- 지난해 보고서는 캐시에 두고, 올해 보고서는 늦게 내는 회사가 있어 부를 때마다 새로 받는다

실행: python -m company_graph.financials load [시작 연도]   (기본 2023. 받고 넣는다)
      python -m company_graph.financials update             (올해 보고서만. 1~5월에는 지난해 사업보고서도. 매일 작업이 서비스 DB에 바로 넣는다)
      python -m company_graph.financials check              (공시 안의 숫자끼리 맞는지)
"""
import sys
from datetime import date, datetime

from sqlalchemy import delete, select

from . import dart
from .cache import cached_json
from .db import Company, Financial, session

REPORTS = {"q1": "11013", "half": "11012", "q3": "11014", "annual": "11011"}
BATCH = 100

# 공시의 계정 이름 → 표의 칸. 금융회사는 매출액 대신 영업수익을 쓴다
ACCOUNTS = {
    "매출액": "revenue", "영업수익": "revenue", "수익(매출액)": "revenue",
    "영업이익": "operating_income", "영업이익(손실)": "operating_income",
    "법인세차감전 순이익": "pretax_income", "법인세차감전 순이익(손실)": "pretax_income", "법인세비용차감전순이익": "pretax_income",
    "당기순이익": "net_income", "당기순이익(손실)": "net_income",
    "유동자산": "current_assets", "비유동자산": "noncurrent_assets", "자산총계": "total_assets",
    "유동부채": "current_liabilities", "비유동부채": "noncurrent_liabilities", "부채총계": "total_liabilities",
    "자본금": "capital_stock", "이익잉여금": "retained_earnings", "이익잉여금(결손금)": "retained_earnings", "자본총계": "total_equity",
}
FLOWS = ("revenue", "operating_income", "pretax_income", "net_income")


def _amount(text: str | None) -> int | None:
    text = (text or "").replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _dates(text: str) -> tuple[date | None, date | None]:
    """'2026.01.01 ~ 2026.06.30' 또는 '2026.06.30 현재'."""
    found = [datetime.strptime(part, "%Y.%m.%d").date() for part in
             __import__("re").findall(r"\d{4}\.\d{2}\.\d{2}", text or "")]
    if len(found) >= 2:
        return found[0], found[1]
    return (None, found[0]) if found else (None, None)


def fetch(corp_codes: list[str], year: int, report: str) -> list[dict]:
    """한 묶음(100곳까지)의 주요계정. 두 해 전까지는 캐시에 둔다 (지난해 사업보고서는 이듬해 봄까지 늦게 내거나 정정하는 회사가 있다)."""
    def call():
        return dart._json("fnlttMultiAcnt.json", corp_code=",".join(corp_codes), bsns_year=str(year),
                          reprt_code=REPORTS[report]).get("list", [])
    if year >= date.today().year - 1:
        return call()
    return cached_json("dart_fin_multi", f"{year}_{report}_{corp_codes[0]}_{corp_codes[-1]}_{len(corp_codes)}", call)


def rows(items: list[dict], ids: dict[str, int], year: int, report: str) -> list[dict]:
    """응답 줄(계정 하나씩)을 (회사, 연결/별도)마다 한 줄로 모은다. 같은 계정이 두 번 오면 앞의 것을 쓴다."""
    out: dict[tuple, dict] = {}
    for item in sorted(items, key=lambda i: int(i.get("ord") or 0)):
        company_id = ids.get(item.get("corp_code"))
        column = ACCOUNTS.get((item.get("account_nm") or "").strip())
        if not company_id or not column:
            continue
        key = (company_id, item["fs_div"])
        row = out.setdefault(key, {"company_id": company_id, "fiscal_year": year, "report": report, "basis": item["fs_div"],
                                   "rcept_no": item["rcept_no"], "currency": item.get("currency") or None})
        if column in row:
            continue
        start, end = _dates(item.get("thstrm_dt"))
        if column in FLOWS:
            row[column] = _amount(item.get("thstrm_amount"))
            if report != "annual":
                row[f"{column}_ytd"] = _amount(item.get("thstrm_add_amount"))
            if column != "pretax_income":
                row[f"{column}_prev"] = _amount(item.get("frmtrm_amount"))
                if report != "annual":
                    row[f"{column}_prev_ytd"] = _amount(item.get("frmtrm_add_amount"))
            if start and not row.get("period_start"):
                row["period_start"], row["period_end"] = start, end
        else:
            row[column] = _amount(item.get("thstrm_amount"))
            if end and not row.get("period_end"):
                row["period_end"] = end
    for row in out.values():
        # 분기·반기 손익의 석 달 시작일: 기간 문자열은 누적 기간(1월 1일~)을 적는 일이 많아 끝날에서 거꾸로 잡는다
        if report != "annual" and row.get("period_end"):
            end = row["period_end"]
            month = end.month - 2
            row["period_start"] = date(end.year if month > 0 else end.year - 1, month if month > 0 else month + 12, 1)
    return list(out.values())


def listed(db) -> dict[str, int]:
    """corp_code → company_id. 유가증권(Y), 코스닥(K), 코넥스(N) 상장사."""
    return dict(db.execute(select(Company.corp_code, Company.company_id)
                           .where(Company.stock_code.is_not(None), Company.corp_code.is_not(None),
                                  Company.corp_cls.in_(("Y", "K", "N")))).all())


def load(start_year: int = 2023) -> None:
    with session() as db:
        ids = listed(db)
        codes = sorted(ids)
        batches = [codes[i:i + BATCH] for i in range(0, len(codes), BATCH)]
        for year in range(start_year, date.today().year + 1):
            for report in REPORTS:
                found = []
                for batch in batches:
                    found += rows(fetch(batch, year, report), ids, year, report)
                if not found:
                    print(f"{year} {report}: 없음", flush=True)
                    continue
                db.execute(delete(Financial).where(Financial.fiscal_year == year, Financial.report == report))
                db.execute(Financial.__table__.insert(), [{c.name: r.get(c.name) for c in Financial.__table__.columns} for r in found])
                db.commit()
                print(f"{year} {report}: {len({r['company_id'] for r in found})}곳 {len(found)}줄 (DART 호출 오늘 {dart.calls_today()}건)", flush=True)


# ---- 조회 ----------------------------------------------------------------------------------------------------

ORDER = ("q1", "half", "q3", "annual")
YTD_LABEL = {"q1": "1분기", "half": "상반기 누적", "q3": "3분기 누적", "annual": "연간"}
BASIS_NAME = {"CFS": "연결", "OFS": "별도"}
RESTATED = 0.01   # 다음 해 보고서가 적은 전년 값이 이만큼 넘게 다르면 다시 작성된 것으로 본다


def filed(row: Financial) -> date:
    return datetime.strptime(row.rcept_no[:8], "%Y%m%d").date()


def unit(currency: str | None) -> tuple[float, str]:
    """금액을 나눌 수와 단위. 원화는 억 원, 외화는 백만 단위."""
    return (1e8, "억원") if (currency or "KRW") == "KRW" else (1e6, f"백만 {currency}")


def _money(value: int | None, divisor: float) -> float | None:
    return None if value is None else round(value / divisor, 1)


def readable(value: float | None, unit_name: str = "억원") -> str:
    """억 원 단위 값 → '171조 4,995억 원'."""
    if value is None:
        return "–"
    if unit_name != "억원":
        return f"{value:,.1f} {unit_name}"
    sign, value = ("-" if value < 0 else ""), abs(value)
    jo, eok = int(value // 10000), round(value % 10000)
    if eok == 10000:
        jo, eok = jo + 1, 0
    if jo:
        return f"{sign}{jo:,}조" + (f" {eok:,}억 원" if eok else " 원")
    if value >= 100:
        return f"{sign}{value:,.0f}억 원"
    return f"{sign}{value:,.1f}억 원" if value >= 1 else f"{sign}{value * 1e4:,.0f}만 원"


def _pct(part, whole) -> float | None:
    return None if part is None or not whole else round(part / whole * 100, 2)


def _change(now, before) -> dict:
    """전년 같은 기간 대비. 앞이 0 이하이면 증감률 대신 흑자·적자 전환을 적는다."""
    if now is None or before is None:
        return {}
    if before > 0:
        return {"yoy_pct": round((now - before) / before * 100, 2)} if now >= 0 else {"yoy": "적자 전환"}
    if before < 0:
        return {"yoy": "흑자 전환" if now > 0 else "적자 지속"}
    return {}


def _differs(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) > RESTATED * max(abs(a), abs(b), 1)


def regroup(rows: list[Financial]) -> dict:
    """보고서를 (결산 연도, 보고서)로 묶는다. 결산월이 12월이 아닌 회사는 DART의 사업연도(bsns_year)가 보고서마다 다른 회계연도를 가리켜서,
    기간의 끝날과 결산월로 다시 정한다. 결산 연도는 회계연도가 끝나는 해다 (6월 결산이면 2025.7~2026.6 이 2026)."""
    ends = [r.period_end.month for r in rows if r.report == "annual" and r.period_end]
    close = max(set(ends), key=ends.count) if ends else 12
    out = {}
    for row in rows:
        if not row.period_end:
            out[(row.fiscal_year, row.report)] = row
            continue
        end = row.period_end
        number = (end.month - close - 1) % 12 // 3 + 1
        year = end.year if end.month <= close else end.year + 1
        out[(year, ORDER[number - 1])] = row
    return out


def label(year: int, close: int, part: str) -> str:
    """'2026 2분기'. 결산월이 12월이 아니면 '2026년 6월 결산 1분기' 처럼 결산을 밝힌다."""
    return f"{year} {part}" if close == 12 else f"{year}년 {close}월 결산 {part}"


def closing_month(by: dict) -> int:
    ends = [r.period_end.month for (_, report), r in by.items() if report == "annual" and r.period_end]
    return max(set(ends), key=ends.count) if ends else 12


def reports(db, company_id: int, as_of: date, basis: str | None = None) -> tuple[str | None, dict]:
    """조회 시점까지 접수된 보고서를 (결산 연도, 보고서)로. basis 를 주지 않으면 연결이 있으면 연결, 없으면 별도."""
    found = [r for r in db.scalars(select(Financial).where(Financial.company_id == company_id)) if filed(r) <= as_of]
    bases = {r.basis for r in found}
    chosen = basis if basis in bases else ("CFS" if "CFS" in bases else "OFS" if bases else None)
    return chosen, regroup([r for r in found if r.basis == chosen])


def restated(by: dict, year: int) -> bool:
    """year 의 분기 값을 다음 해 보고서가 다르게 적었나. 그러면 연간에서 1~3분기를 빼서 4분기를 만들 수 없다."""
    pairs = (("q1", "revenue", "revenue_prev"), ("q1", "operating_income", "operating_income_prev"),
             ("half", "revenue_ytd", "revenue_prev_ytd"), ("half", "operating_income_ytd", "operating_income_prev_ytd"),
             ("q3", "revenue_ytd", "revenue_prev_ytd"), ("q3", "operating_income_ytd", "operating_income_prev_ytd"))
    for report, mine, theirs in pairs:
        this, later = by.get((year, report)), by.get((year + 1, report))
        if this and later and _differs(getattr(this, mine), getattr(later, theirs)):
            return True
    return False


def quarters(by: dict, divisor: float) -> list[dict]:
    """분기마다 석 달 값. 4분기는 연간 - 3분기 누적으로 계산한다."""
    out, close = [], closing_month(by)
    for year in sorted({y for y, _ in by}):
        for number, report in enumerate(ORDER, 1):
            row = by.get((year, report))
            if not row:
                continue
            item = {"period": label(year, close, f"{number}분기"), "fiscal_year": year, "quarter": number,
                    "end": row.period_end.isoformat() if row.period_end else None, "rcept_no": [row.rcept_no]}
            if report == "annual":
                q3 = by.get((year, "q3"))
                if not q3:
                    continue
                if restated(by, year):
                    item["not_computed"] = (f"{year}년 1~3분기 값을 다음 해 보고서가 다시 작성해서(사업 분할, 회계 변경 등) "
                                            "연간에서 빼는 방식으로 4분기를 계산하지 않았습니다")
                    out.append(item)
                    continue
                item["computed"] = "4분기는 공시되지 않아 사업보고서의 연간 값에서 3분기보고서의 누적 값을 뺐습니다"
                item["rcept_no"].append(q3.rcept_no)
                values = {k: (getattr(row, k) - getattr(q3, f"{k}_ytd")) if getattr(row, k) is not None and getattr(q3, f"{k}_ytd") is not None else None
                          for k in ("revenue", "operating_income", "net_income")}
                before = {k: (getattr(row, f"{k}_prev") - getattr(q3, f"{k}_prev_ytd")) if getattr(row, f"{k}_prev") is not None and getattr(q3, f"{k}_prev_ytd") is not None else None
                          for k in ("revenue", "operating_income", "net_income")}
            else:
                values = {k: getattr(row, k) for k in ("revenue", "operating_income", "net_income")}
                before = {k: getattr(row, f"{k}_prev") for k in ("revenue", "operating_income", "net_income")}
            for key, value in values.items():
                item[key] = _money(value, divisor)
                item.update({f"{key}_{k}": v for k, v in _change(value, before[key]).items()})
            item["operating_margin_pct"] = _pct(values["operating_income"], values["revenue"])
            out.append(item)
    return out


def years(by: dict, divisor: float) -> list[dict]:
    """사업연도마다 가장 나중 보고서의 누적 값 (연간이 나왔으면 연간, 아니면 '상반기 누적' 처럼)."""
    out, close = [], closing_month(by)
    for year in sorted({y for y, _ in by}):
        report = next(r for r in reversed(ORDER) if (year, r) in by)
        row = by[(year, report)]
        ytd = "" if report in ("annual", "q1") else "_ytd"
        item = {"period": label(year, close, YTD_LABEL[report]), "fiscal_year": year, "through": report, "rcept_no": [row.rcept_no]}
        for key in ("revenue", "operating_income", "net_income"):
            now, before = getattr(row, key + ytd), getattr(row, f"{key}_prev{ytd}")
            item[key] = _money(now, divisor)
            item.update({f"{key}_{k}": v for k, v in _change(now, before).items()})
        item["operating_margin_pct"] = _pct(getattr(row, "operating_income" + ytd), getattr(row, "revenue" + ytd))
        item["net_margin_pct"] = _pct(getattr(row, "net_income" + ytd), getattr(row, "revenue" + ytd))
        out.append(item)
    return out


def balance(row: Financial, divisor: float) -> dict:
    return {"date": row.period_end.isoformat() if row.period_end else None, "rcept_no": [row.rcept_no],
            "total_assets": _money(row.total_assets, divisor), "total_liabilities": _money(row.total_liabilities, divisor),
            "total_equity": _money(row.total_equity, divisor), "current_assets": _money(row.current_assets, divisor),
            "current_liabilities": _money(row.current_liabilities, divisor),
            "debt_ratio_pct": _pct(row.total_liabilities, row.total_equity) if (row.total_equity or 0) > 0 else None,
            "current_ratio_pct": _pct(row.current_assets, row.current_liabilities),
            **({"capital_impaired": "자본총계가 0 이하(자본잠식)라 부채비율을 계산하지 않았습니다"} if row.total_equity is not None and row.total_equity <= 0 else {})}


def latest(by: dict) -> Financial | None:
    return max(by.values(), key=lambda r: (r.period_end or date.min, ORDER.index(r.report))) if by else None


_MEDIANS: dict = {}


def medians(db, as_of: date, market: str | None) -> dict:
    """같은 시장 상장사들의 가장 최근 보고서 기준 중앙값 (부채비율, 유동비율, 영업이익률). 조회 시점마다 한 번 계산한다."""
    import statistics
    key = (as_of, market)
    if key in _MEDIANS:
        return _MEDIANS[key]
    query = select(Financial, Company.corp_cls).join(Company, Company.company_id == Financial.company_id)
    newest: dict = {}
    for row, cls in db.execute(query):
        if market and cls != market or filed(row) > as_of:
            continue
        best = newest.get(row.company_id)
        rank = (row.basis == "CFS", row.period_end or date.min, ORDER.index(row.report))
        if not best or rank > best[0]:
            newest[row.company_id] = (rank, row)
    debt, current, margin = [], [], []
    for _, row in newest.values():
        if (row.total_equity or 0) > 0 and row.total_liabilities is not None:
            debt.append(row.total_liabilities / row.total_equity * 100)
        if row.current_liabilities and row.current_assets is not None:
            current.append(row.current_assets / row.current_liabilities * 100)
        revenue = row.revenue_ytd if row.report in ("half", "q3") else row.revenue
        income = row.operating_income_ytd if row.report in ("half", "q3") else row.operating_income
        if revenue and revenue > 0 and income is not None:
            margin.append(income / revenue * 100)
    result = {"companies": len(newest),
              "debt_ratio_pct": round(statistics.median(debt), 2) if debt else None,
              "current_ratio_pct": round(statistics.median(current), 2) if current else None,
              "operating_margin_pct": round(statistics.median(margin), 2) if margin else None}
    _MEDIANS[key] = result
    return result


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "load"
    if command == "load":
        load(int(sys.argv[2]) if len(sys.argv) > 2 else 2023)
    elif command == "update":
        today = date.today()
        load(today.year - 1 if today.month <= 5 else today.year)
    elif command == "check":
        from .check_financials import main
        main()
