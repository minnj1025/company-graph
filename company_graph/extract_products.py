"""정기보고서 "주요 제품 및 서비스"의 매출 비중 표를 읽어 제품 줄로 DB에 담는다.

이미 담아 둔 "II. 사업의 내용"(business_section)의 글에서 읽으므로 DART를 다시 부르지 않는다.
읽은 비중의 합이 100에 가깝지 않은 표는 담지 않는다. 그런 회사는 "제품 표를 읽지 못함"으로 남는다(지어내지 않는다).
표준 이름은 여기서 붙이지 않는다. 제품이라 할 것이 없는 줄("기타", "상품", "임대")만 규칙으로 가려 두고,
나머지는 name_products 가 붙인다. 다시 돌려도 이미 붙인 표준 이름은 그대로 이어받는다.

실행: python -m company_graph.extract_products
"""
from collections import Counter

from sqlalchemy import delete, select

from .db import BusinessSection, Product, init_db, session
from .product_names import candidate
from .product_parser import products

SECTION = "주요 제품"


def main():
    engine = init_db()
    stats = Counter()
    with session(engine) as db:
        # (사업부문, 이름) → 이미 붙인 표준 이름. 같은 글자의 줄은 다시 묻지 않는다
        named = {(p.segment, p.name): (p.std_names, p.named_by)
                 for p in db.scalars(select(Product).where(Product.named_by.is_not(None)))}
        sections = db.scalars(select(BusinessSection).where(BusinessSection.title.like(f"%{SECTION}%"))
                              .order_by(BusinessSection.rcept_no, BusinessSection.section_no)).all()
        db.execute(delete(Product))
        seen = set()
        for section in sections:
            if section.rcept_no in seen:   # 한 보고서에 같은 소제목이 둘이면 앞의 것
                continue
            seen.add(section.rcept_no)
            stats["보고서"] += 1
            rows = products(section.text)
            if not rows:
                stats["표를 읽지 못한 보고서"] += 1
                continue
            stats["표를 읽은 보고서"] += 1
            for row_no, row in enumerate(rows, 1):
                key = (row.segment and row.segment[:100], row.name)
                std_names, named_by = named.get(key, (None, None))
                if named_by is None and candidate(*key) is None:
                    std_names, named_by = [], "rule"
                    stats["제품이라 할 것이 없는 줄"] += 1
                stats["담은 줄"] += 1
                stats["표준 이름이 아직 없는 줄"] += named_by is None
                db.add(Product(company_id=section.company_id, rcept_no=section.rcept_no, bsns_year=section.bsns_year,
                               disclosed_date=section.disclosed_date, row_no=row_no, segment=key[0], name=row.name,
                               share_pct=row.share, std_names=std_names, named_by=named_by))
        db.commit()
        stats["표를 읽은 회사"] = len(set(db.scalars(select(Product.company_id))))
        stats["주요 제품 절이 있는 회사"] = len({s.company_id for s in sections})
    for key, value in sorted(stats.items()):
        print(f"{key}: {value:,}")


if __name__ == "__main__":
    main()
