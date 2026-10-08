"""2단계: 지분 관계. 사업보고서의 두 표(DART API)를 relation 표에 넣는다.

- 타법인 출자현황: 보고서를 낸 회사가 보유한 지분 (주체 = 보고서를 낸 회사)
- 최대주주 현황: 보고서를 낸 회사를 보유한 주주 (상대 = 보고서를 낸 회사)

같은 지분이 두 표에 다 나올 수 있다. 한쪽 표에서 빠진 지분을 다른 쪽이 채우므로 둘 다 넣고,
어느 표에서 왔는지 attrs.source 에 적는다. 조회할 때 (주체, 상대, 기준일)로 합친다.

실행: python -m company_graph.extract_equity
전체 상장사로 넓히면 회사 × 연도 × 표 2개 = 약 2,650 × 3 × 2 ≈ 16,000건으로 하루 한도(20,000건)에 가깝다.
받은 것은 캐시에 남으므로 한도에 걸리면 다음 날 같은 명령으로 이어 받는다.
"""
import re
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, select

from . import dart, loader
from .db import Company, CompanyAlias, Document, QualityLog, Relation, init_db, session
from .manual_aliases import SHORT_OK
from .names import clean_reported, normalize

YEARS = (2023, 2024, 2025)
LISTED = ("Y", "K", "N")
VERSION = "equity-3"  # 법인 표시가 붙은 영문 약어는 붙임. 3: 원장에 없는 주주(개인 등)도 이름으로 넣음. 2: 자기 계열회사 표를 먼저 보고 연결, 적힌 그대로의 지분율 보존, 줄을 지우지 않음


def to_decimal(text: str | None) -> Decimal | None:
    cleaned = (text or "").replace(",", "").strip()
    if cleaned in ("", "-"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def rcept_date(rcept_no: str) -> date:
    """접수번호 앞 8자리가 접수일이다."""
    return datetime.strptime(rcept_no[:8], "%Y%m%d").date()


_CORPORATE_MARK = re.compile(r"㈜|\(주\)|주식회사|\(유\)|유한회사")
_SHORT_OK = {normalize(name) for name in SHORT_OK}


def alias_index(db) -> dict[str, set[int]]:
    """별칭 → 기업. 같은 이름이 여러 회사에 걸리면, 지금 상장된 회사가 하나뿐일 때만 그 회사로 본다.

    원장을 전체 상장사로 넓히면 상장폐지된 옛 회사와 이름이 겹치는 경우가 생긴다.
    """
    listed = set(db.scalars(select(Company.company_id).where(Company.corp_cls.in_(("Y", "K", "N")))))
    index: dict[str, set[int]] = {}
    for alias, company_id in db.execute(select(CompanyAlias.alias, CompanyAlias.company_id)):
        index.setdefault(alias, set()).add(company_id)
    for alias, ids in index.items():
        if len(ids) > 1 and len(ids & listed) == 1:
            index[alias] = ids & listed
    return index


def link(index: dict[str, set[int]], raw_name: str) -> int | None:
    """공시에 적힌 이름을 원장의 기업에 붙인다. 애매하면 붙이지 않는다.

    - 후보가 둘 이상이면 붙이지 않는다
    - 영문 네 글자 이하는 붙이지 않는다. 회사들이 해외 법인을 약어로 적는데(현대자동차의 "HMM"은 멕시코 법인),
      같은 약어를 종목명으로 쓰는 상장사(해운사 HMM)에 잘못 붙는다.
      다만 "HDC㈜", "(주)KNN"처럼 국내 법인 표시가 같이 적혀 있으면 약어가 아니라 회사 이름이므로 붙인다.
      표시 없이 쓰이는 것 중 확인한 것은 manual_aliases.SHORT_OK 에 적는다
    """
    key = normalize(clean_reported(raw_name))
    if key.isascii() and len(key) <= 4 and not _CORPORATE_MARK.search(raw_name) and key not in _SHORT_OK:
        return None
    candidates = index.get(key, set())
    return next(iter(candidates)) if len(candidates) == 1 else None


class ReportNames:
    """접수번호 → 보고서명. 회사마다 정기공시 목록을 한 번만 받는다."""

    def __init__(self):
        self._by_company: dict[str, dict[str, str]] = {}

    def get(self, corp_code: str, rcept_no: str, year: int) -> str:
        if corp_code not in self._by_company:
            self._by_company[corp_code] = {f["rcept_no"]: f["report_nm"].strip() for f in dart.filings(
                corp_code, bgn_de=f"{YEARS[0] + 1}0101", end_de=date.today().strftime("%Y%m%d"), pblntf_ty="A")}
        return self._by_company[corp_code].get(rcept_no, f"사업보고서 ({year}.12)")


def upsert_document(db, company: Company, rcept_no: str, year: int, names: ReportNames) -> Document:
    doc = db.get(Document, rcept_no)
    if doc is None:
        report_nm = names.get(company.corp_code, rcept_no, year)
        doc = Document(rcept_no=rcept_no, company_id=company.company_id, report_nm=report_nm,
                       rcept_dt=rcept_date(rcept_no), doc_type="annual", bsns_year=year,
                       is_correction="정정]" in report_nm, fetched_at=datetime.now())
        db.add(doc)
        if doc.is_correction:
            # API는 최신본만 준다. 정정 전 원본의 공개일을 잃으므로 기록해 둔다
            db.add(QualityLog(check_name="correction", rcept_no=rcept_no, created_at=datetime.now(),
                              detail=f"{company.name} {year} 사업보고서가 정정본이라 공개일이 정정일로 잡힘"))
        db.flush()
    return doc


def in_range(db, pct: Decimal, rcept_no: str, what: str) -> bool:
    if 0 <= pct <= 100:
        return True
    db.add(QualityLog(check_name="range", rcept_no=rcept_no, created_at=datetime.now(), detail=f"{what}: 지분율 {pct}"))
    return False


def as_of(row: dict, year: int) -> date:
    return datetime.strptime(row.get("stlm_dt") or f"{year}-12-31", "%Y-%m-%d").date()


def own_affiliates(db, company: Company) -> dict[str, int]:
    """이 회사가 사업보고서의 계열회사 표에 직접 적은 이름 → 기업. 그 표에는 법인등록번호가 있어 이름이 겹칠 걱정이 없다."""
    found: dict[str, set[int]] = {}
    for name, company_id in db.execute(select(Relation.object_name_raw, Relation.object_company_id).where(
            Relation.rel_type == "affiliate", Relation.subject_company_id == company.company_id,
            Relation.object_company_id.is_not(None), Relation.retired_at.is_(None)).distinct()):
        found.setdefault(normalize(clean_reported(name)), set()).add(company_id)
    return {key: next(iter(ids)) for key, ids in found.items() if len(ids) == 1}


def link_in_context(index: dict[str, set[int]], affiliates: dict[str, int], raw_name: str) -> tuple[int | None, str | None]:
    """식별자가 있는 근거를 먼저 쓴다: ① 그 회사 자신의 계열회사 표 ② 별칭이 정확히 하나에 맞을 때.
    돌려주는 두 번째 값은 왜 붙였는지다."""
    key = normalize(clean_reported(raw_name))
    if key in affiliates:
        return affiliates[key], "own_affiliate_table"
    company_id = link(index, raw_name)
    return company_id, "alias_exact" if company_id else None


def load_investments(db, company: Company, year: int, index, affiliates, names: ReportNames, stats: Counter):
    rows = dart.other_corp_investments(company.corp_code, year)
    if not rows:
        return
    stats["출자현황이 있는 사업보고서"] += 1
    rcept_no = rows[0]["rcept_no"]
    doc = upsert_document(db, company, rcept_no, year, names)
    new_rows = []
    for row in rows:
        name = " ".join((row.get("inv_prm") or "").split())
        pct = to_decimal(row.get("trmend_blce_qota_rt"))
        if not name or name in ("합계", "계"):
            continue
        if pct is None:
            stats["출자현황: 기말 지분 없음(처분 등)"] += 1
            continue
        if not in_range(db, pct, rcept_no, f"{company.name} → {name}"):
            continue
        object_id, reason = link_in_context(index, affiliates, name)
        stats[f"출자현황: 상대를 연결 ({reason})" if object_id else "출자현황: 상대가 원장에 없음"] += 1
        new_rows.append(Relation(
            subject_company_id=company.company_id, object_company_id=object_id, object_name_raw=name,
            rel_type="equity", value_num=pct, value_unit="pct", as_of_date=as_of(row, year),
            disclosed_date=doc.rcept_dt, rcept_no=rcept_no, extract_method="api", trust_tier=1,
            attrs={"source": "other_corp_investments", "link_reason": reason,
                   "raw_pct": (row.get("trmend_blce_qota_rt") or "").strip(), "purpose": row.get("invstmnt_purps"),
                   "first_acquired": row.get("frst_acqs_de"), "shares": row.get("trmend_blce_qy"),
                   "book_value": row.get("trmend_blce_acntbk_amount")}))
    # 같은 보고서에서 extract_invest_detail 이 넣은 줄(표준 표 밖의 출자)은 건드리지 않는다
    counts = loader.sync(db, [Relation.rcept_no == rcept_no, Relation.rel_type == "equity",
                              Relation.subject_company_id == company.company_id,
                              Relation.attrs["source"].as_string() == "other_corp_investments"], new_rows, VERSION)
    stats.update({f"출자현황 줄: {k}": v for k, v in counts.items()})


def load_shareholders(db, company: Company, year: int, index, affiliates, names: ReportNames, stats: Counter):
    rows = dart.largest_shareholders(company.corp_code, year)
    if not rows:
        return
    stats["최대주주 현황이 있는 사업보고서"] += 1
    rcept_no = rows[0]["rcept_no"]
    doc = upsert_document(db, company, rcept_no, year, names)
    new_rows = []
    for row in rows:
        name = " ".join((row.get("nm") or "").split())
        pct = to_decimal(row.get("trmend_posesn_stock_qota_rt"))
        # 보통주만 넣는다. 우선주 줄까지 넣으면 한 주주가 두 줄이 된다
        if not name or name in ("합계", "계") or not pct or "우선" in (row.get("stock_knd") or ""):
            continue
        holder_id, reason = link_in_context(index, affiliates, name)
        if holder_id == company.company_id or not in_range(db, pct, rcept_no, f"{name} → {company.name}"):
            continue
        # 원장에 없는 주주(개인, 정부, 비상장·해외 법인)도 이름 그대로 넣는다. 최대주주가 개인인 회사가 많다
        stats[f"최대주주 현황: 주주를 연결 ({reason})" if holder_id else "최대주주 현황: 주주가 원장에 없음(개인, 정부, 비상장 등)"] += 1
        new_rows.append(Relation(
            subject_company_id=holder_id, subject_name_raw=None if holder_id else name[:300],
            object_company_id=company.company_id, object_name_raw=company.name,
            rel_type="equity", value_num=pct, value_unit="pct", as_of_date=as_of(row, year),
            disclosed_date=doc.rcept_dt, rcept_no=rcept_no, extract_method="api", trust_tier=1,
            attrs={"source": "largest_shareholders", "link_reason": reason, "holder_name_raw": name,
                   "raw_pct": (row.get("trmend_posesn_stock_qota_rt") or "").strip(),
                   "relation_to_filer": row.get("relate"), "shares": row.get("trmend_posesn_stock_co")}))
    counts = loader.sync(db, [Relation.rcept_no == rcept_no, Relation.rel_type == "equity",
                              Relation.object_company_id == company.company_id,
                              or_(Relation.subject_company_id.is_(None),
                                  Relation.subject_company_id != company.company_id)], new_rows, VERSION)
    stats.update({f"최대주주 현황 줄: {k}": v for k, v in counts.items()})


def main(everyone: bool):
    engine = init_db()
    stats, names = Counter(), ReportNames()
    with session(engine) as db:
        index = alias_index(db)
        # --all 이면 지금 상장된 회사 전부(유가증권 Y, 코스닥 K, 코넥스 N). 수집 대상 기업을 먼저 돈다
        wanted = or_(Company.in_scope, Company.corp_cls.in_(LISTED)) if everyone else Company.in_scope
        companies = db.scalars(select(Company).where(wanted, Company.corp_code.is_not(None))
                               .order_by(Company.in_scope.desc(), Company.company_id)).all()
        print(f"회사 {len(companies)}곳", flush=True)
        try:
            for n, company in enumerate(companies, 1):
                affiliates = own_affiliates(db, company)
                try:
                    for year in YEARS:
                        load_investments(db, company, year, index, affiliates, names, stats)
                        load_shareholders(db, company, year, index, affiliates, names, stats)
                except dart.DailyBudgetExceeded:
                    raise
                except dart.DartError as error:
                    db.rollback()
                    stats["API 오류로 건너뛴 회사"] += 1
                    print(f"  건너뜀 {company.name}: {error}", flush=True)
                    continue
                db.commit()
                if n % 200 == 0:
                    print(f"  {n}/{len(companies)}곳, 오늘 DART 호출 {dart.calls_today()}건", flush=True)
        except dart.DailyBudgetExceeded as stop:
            db.rollback()
            print(f"중단: {stop}. 받은 것은 캐시에 있으니 내일 같은 명령으로 이어 받는다")
    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")
    print(f"오늘 DART 호출 {dart.calls_today()}건")


if __name__ == "__main__":
    main(everyone="--all" in sys.argv[1:])
