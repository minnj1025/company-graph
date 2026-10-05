"""2단계: 지분 관계. 사업보고서의 두 표(DART API)를 relation 표에 넣는다.

- 타법인 출자현황: 보고서를 낸 회사가 보유한 지분 (주체 = 보고서를 낸 회사)
- 최대주주 현황: 보고서를 낸 회사를 보유한 주주 (상대 = 보고서를 낸 회사)

같은 지분이 두 표에 다 나올 수 있다. 한쪽 표에서 빠진 지분을 다른 쪽이 채우므로 둘 다 넣고,
어느 표에서 왔는지 attrs.source 에 적는다. 조회할 때 (주체, 상대, 기준일)로 합친다.

실행: python -m company_graph.extract_equity
전체 상장사로 넓히면 회사 × 연도 × 표 2개 = 약 2,650 × 3 × 2 ≈ 16,000건으로 하루 한도(20,000건)에 가깝다.
받은 것은 캐시에 남으므로 한도에 걸리면 다음 날 같은 명령으로 이어 받는다.
"""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select

from . import dart
from .db import Company, CompanyAlias, Document, QualityLog, Relation, init_db, session
from .names import clean_reported, normalize

YEARS = (2023, 2024, 2025)


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
    """공시에 적힌 이름을 대상 기업에 붙인다. 후보가 둘 이상이면 붙이지 않는다."""
    candidates = index.get(normalize(clean_reported(raw_name)), set())
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


def load_investments(db, company: Company, year: int, index, names: ReportNames, stats: Counter):
    rows = dart.other_corp_investments(company.corp_code, year)
    if not rows:
        return
    stats["출자현황이 있는 사업보고서"] += 1
    rcept_no = rows[0]["rcept_no"]
    doc = upsert_document(db, company, rcept_no, year, names)
    # 다시 돌려도 중복되지 않게 이 공시에서 나온 줄을 지우고 새로 넣는다
    db.execute(delete(Relation).where(Relation.rcept_no == rcept_no, Relation.rel_type == "equity",
                                      Relation.subject_company_id == company.company_id))
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
        object_id = link(index, name)
        stats["출자현황: 상대를 원장에 연결" if object_id else "출자현황: 상대가 원장에 없음"] += 1
        db.add(Relation(
            subject_company_id=company.company_id, object_company_id=object_id, object_name_raw=name,
            rel_type="equity", value_num=pct, value_unit="pct", as_of_date=as_of(row, year),
            disclosed_date=doc.rcept_dt, rcept_no=rcept_no, extract_method="api", trust_tier=1,
            attrs={"source": "other_corp_investments", "purpose": row.get("invstmnt_purps"),
                   "first_acquired": row.get("frst_acqs_de"), "shares": row.get("trmend_blce_qy"),
                   "book_value": row.get("trmend_blce_acntbk_amount")}))


def load_shareholders(db, company: Company, year: int, index, names: ReportNames, stats: Counter):
    rows = dart.largest_shareholders(company.corp_code, year)
    if not rows:
        return
    stats["최대주주 현황이 있는 사업보고서"] += 1
    rcept_no = rows[0]["rcept_no"]
    doc = upsert_document(db, company, rcept_no, year, names)
    db.execute(delete(Relation).where(Relation.rcept_no == rcept_no, Relation.rel_type == "equity",
                                      Relation.object_company_id == company.company_id))
    for row in rows:
        name = " ".join((row.get("nm") or "").split())
        pct = to_decimal(row.get("trmend_posesn_stock_qota_rt"))
        # 보통주만 넣는다. 우선주 줄까지 넣으면 한 주주가 두 줄이 된다
        if not name or name in ("합계", "계") or not pct or "우선" in (row.get("stock_knd") or ""):
            continue
        holder_id = link(index, name)
        if holder_id is None:
            stats["최대주주 현황: 주주가 원장에 없음(개인, 비상장 등)"] += 1
            continue
        if holder_id == company.company_id or not in_range(db, pct, rcept_no, f"{name} → {company.name}"):
            continue
        stats["최대주주 현황: 주주를 원장에 연결"] += 1
        db.add(Relation(
            subject_company_id=holder_id, object_company_id=company.company_id, object_name_raw=company.name,
            rel_type="equity", value_num=pct, value_unit="pct", as_of_date=as_of(row, year),
            disclosed_date=doc.rcept_dt, rcept_no=rcept_no, extract_method="api", trust_tier=1,
            attrs={"source": "largest_shareholders", "holder_name_raw": name, "relation_to_filer": row.get("relate"),
                   "shares": row.get("trmend_posesn_stock_co")}))


def main():
    engine = init_db()
    stats, names = Counter(), ReportNames()
    with session(engine) as db:
        index = alias_index(db)
        companies = db.scalars(select(Company).where(Company.in_scope, Company.corp_code.is_not(None))).all()
        for company in companies:
            for year in YEARS:
                load_investments(db, company, year, index, names, stats)
                load_shareholders(db, company, year, index, names, stats)
            db.commit()
    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")
    print(f"오늘 DART 호출 {dart.calls_today()}건")


if __name__ == "__main__":
    main()
