"""사업보고서의 "II. 사업의 내용"을 소제목 단위로 DB에 담는다. 원문은 계열회사 표를 읽을 때 받아 둔 것을 쓴다.

규칙으로 자르기만 하고 요약하거나 값을 뽑지 않는다. 해석은 질문이 올 때 Agent가 근거 문장과 함께 한다.

실행: python -m company_graph.extract_business [사업연도 …]   (기본 2025)
"""
import sys
from collections import Counter

from sqlalchemy import delete, select

from . import dart
from .business_parser import sections
from .db import BusinessSection, Company, Document, init_db, session

SKIP = ("위험관리",)   # 파생상품과 환위험 설명. 회사가 무엇을 하는지와 거리가 멀고 길다
STANDARD = ("사업의 개요", "주요 제품", "원재료", "매출")


def main(years: list[int]):
    engine = init_db()
    stats = Counter()
    with session(engine) as db:
        docs = db.scalars(select(Document).where(Document.doc_type == "annual", Document.bsns_year.in_(years))
                          .order_by(Document.rcept_no)).all()
        for n, doc in enumerate(docs, 1):
            if n % 500 == 0:
                db.commit()
                print(f"  {n}/{len(docs)}건, 오늘 DART 호출 {dart.calls_today()}건", flush=True)
            try:
                found = sections(dart.document(doc.rcept_no))
            except dart.DailyBudgetExceeded as stop:
                print(f"중단: {stop}. 내일 같은 명령으로 이어 받는다")
                break
            except dart.DartError as error:
                stats["원문을 받지 못함"] += 1
                print(f"  {db.get(Company, doc.company_id).name}: {error}")
                continue
            stats["사업보고서"] += 1
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
    main([int(a) for a in sys.argv[1:]] or [2025])
