"""정기보고서의 "II. 사업의 내용"을 소제목 단위로 DB에 담는다.

규칙으로 자르기만 하고 요약하거나 값을 뽑지 않는다. 해석은 질문이 올 때 Agent가 근거 문장과 함께 한다.

실행: python -m company_graph.extract_business annual 2025     사업보고서 (원문은 계열회사 표를 읽을 때 받아 둔 것)
      python -m company_graph.extract_business half 2026       반기보고서 (공시 목록에서 찾아 등록하고 원문을 받는다)
"""
import sys
from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select

from . import dart
from .business_parser import sections
from .cache import cached_json
from .db import BusinessSection, Company, Document, init_db, session
from .extract_equity import rcept_date

SKIP = ("위험관리",)   # 파생상품과 환위험 설명. 회사가 무엇을 하는지와 거리가 멀고 길다
STANDARD = ("사업의 개요", "주요 제품", "원재료", "매출")
LISTED = ("Y", "K", "N")
# 보고서 종류마다: 공시 상세유형, 보고서 이름, 결산월(12월 결산 기준), 공시가 나오는 기간
KINDS = {"half": ("A002", "반기보고서", "06", ("0701", "1231")),
         "quarter": ("A003", "분기보고서", None, ("0401", "1231"))}


def register(db, kind: str, year: int, stats: Counter) -> None:
    """그 해의 반기(분기)보고서를 공시 목록에서 찾아 문서로 등록한다. 회사마다 본문이 있는 가장 나중 것 하나."""
    detail_type, name, month, (start, end) = KINDS[kind]
    today = date.today()
    last = min(date(year, int(end[:2]), int(end[2:])), today)
    rows, cursor = [], date(year, int(start[:2]), int(start[2:]))
    while cursor <= last:   # 회사를 정하지 않은 목록 조회는 석 달까지만 된다
        until = min(cursor + timedelta(days=80), last)
        key = f"{detail_type}_{cursor:%Y%m%d}_{until:%Y%m%d}"
        rows += cached_json("dart_filings_reports", key, lambda: dart.filings(
            bgn_de=f"{cursor:%Y%m%d}", end_de=f"{until:%Y%m%d}", pblntf_detail_ty=detail_type))
        cursor = until + timedelta(days=1)
    wanted = f"{name} ({year}.{month})" if month else f"{name} ({year}."
    by_company: dict[str, list[dict]] = {}
    for row in rows:
        # [첨부정정]은 첨부만 고친 것이라 본문이 없다
        if wanted in row["report_nm"] and "기한연장" not in row["report_nm"] and "[첨부정정]" not in row["report_nm"]:
            by_company.setdefault(row["corp_code"], []).append(row)
    companies = {c.corp_code: c for c in db.scalars(select(Company).where(Company.corp_code.in_(list(by_company)),
                                                                          Company.corp_cls.in_(LISTED)))}
    stats["목록에 있으나 상장사 원장에 없는 회사"] = len(by_company) - len(companies)
    for corp_code, company in companies.items():
        row = max(by_company[corp_code], key=lambda r: r["rcept_no"])
        if db.get(Document, row["rcept_no"]) is None:
            report_nm = row["report_nm"].strip()
            db.add(Document(rcept_no=row["rcept_no"], company_id=company.company_id, report_nm=report_nm,
                            rcept_dt=rcept_date(row["rcept_no"]), doc_type=kind, bsns_year=year,
                            is_correction="정정]" in report_nm, fetched_at=datetime.now()))
            stats["새로 등록한 보고서"] += 1
    db.commit()


def main(kind: str, years: list[int]):
    engine = init_db()
    stats = Counter()
    with session(engine) as db:
        if kind != "annual":
            for year in years:
                register(db, kind, year, stats)
        docs = db.scalars(select(Document).where(Document.doc_type == kind, Document.bsns_year.in_(years))
                          .order_by(Document.rcept_no)).all()
        done = set(db.scalars(select(BusinessSection.rcept_no).distinct())) if kind != "annual" else set()
        for n, doc in enumerate(docs, 1):
            if n % 500 == 0:
                db.commit()
                print(f"  {n}/{len(docs)}건, 오늘 DART 호출 {dart.calls_today()}건", flush=True)
            if doc.rcept_no in done:   # 이어 받을 때 이미 담은 것은 건너뛴다
                stats["이미 담은 보고서"] += 1
                continue
            try:
                found = sections(dart.document(doc.rcept_no))
            except dart.DailyBudgetExceeded as stop:
                print(f"중단: {stop}. 내일 같은 명령으로 이어 받는다")
                break
            except dart.DartError as error:
                stats["원문을 받지 못함"] += 1
                print(f"  {db.get(Company, doc.company_id).name}: {error}")
                continue
            stats["보고서"] += 1
            if not found:
                stats["사업의 내용을 찾지 못함"] += 1
                continue
            titles = " ".join(s.title for s in found)
            stats["표준 소제목 네 개가 다 있음" if all(word in titles for word in STANDARD) else "소제목이 표준과 다름"] += 1
            db.execute(delete(BusinessSection).where(BusinessSection.rcept_no == doc.rcept_no))
            seen = set()
            for section in found:
                if any(word in section.title for word in SKIP) or section.number in seen:
                    continue
                seen.add(section.number)
                db.add(BusinessSection(company_id=doc.company_id, rcept_no=doc.rcept_no, bsns_year=doc.bsns_year,
                                       disclosed_date=doc.rcept_dt, section_no=section.number, title=section.title,
                                       text=section.text, truncated=section.truncated))
                stats["담은 절"] += 1
                stats["담은 글자"] += len(section.text)
                stats["길어서 자른 절"] += section.truncated
        db.commit()
    for key, value in sorted(stats.items()):
        print(f"{key}: {value:,}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "annual", [int(a) for a in sys.argv[2:]] or [2025])
