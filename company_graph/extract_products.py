"""정기보고서의 매출 비중 표를 읽어 제품 줄로 DB에 담는다.

이미 담아 둔 "II. 사업의 내용"(business_section)의 글에서 읽으므로 DART를 다시 부르지 않는다.
읽는 차례는 product_parser.read_report 에 있다: "주요 제품" 절의 비중 표, 같은 절의 매출액 표(합계로 나눠 비중을 구함),
그리고 어느 보고서에서도 그 절을 읽지 못한 회사에 한해 "매출 및 수주상황" 절의 품목별 표.
더해서 100이 되는 묶음을 찾지 못하면 담지 않는다. 그런 회사는 "제품 표를 읽지 못함"으로 남는다(지어내지 않는다).

표준 이름은 여기서 붙이지 않는다. 제품이라 할 것이 없는 줄("기타", "상품", "임대")만 규칙으로 가려 두고,
나머지는 product_naming 이 붙인다. 다시 돌려도 이미 붙인 표준 이름과 제품군은 그대로 이어받는다.
읽는 규칙을 고쳐서 전에 읽던 보고서를 못 읽게 되면, 그 보고서의 줄은 지우지 않고 둔다.

실행: python -m company_graph.extract_products
"""
from collections import Counter, defaultdict

from sqlalchemy import delete, select

from .db import BusinessSection, Product, init_db, session
from .product_names import candidate
from .product_parser import read_report


def main():
    engine = init_db()
    stats = Counter()
    with session(engine) as db:
        # (사업부문, 이름) → 이미 붙인 표준 이름. 같은 글자의 줄은 다시 묻지 않는다
        named = {(p.segment, p.name): (p.std_names, p.std_families, p.named_by, p.unsure)
                 for p in db.scalars(select(Product).where(Product.named_by.is_not(None)))}
        had = set(db.scalars(select(Product.rcept_no).distinct()))
        reports: dict[str, list[BusinessSection]] = defaultdict(list)
        for section in db.scalars(select(BusinessSection).order_by(BusinessSection.rcept_no, BusinessSection.section_no)):
            reports[section.rcept_no].append(section)
        stats["보고서"] = len(reports)

        read: dict[str, tuple[list, str]] = {}
        for rcept_no, sections in reports.items():
            found = read_report([(s.title, s.text) for s in sections])
            if found:
                read[rcept_no] = found
        # "주요 제품" 절을 어느 보고서에서도 읽지 못한 회사만 "매출" 절을 본다
        covered = {reports[rcept_no][0].company_id for rcept_no in read} | {reports[r][0].company_id for r in had if r in reports}
        for rcept_no, sections in reports.items():
            if sections[0].company_id not in covered:
                found = read_report([(s.title, s.text) for s in sections], fallback=True)
                if found:
                    read[rcept_no] = found

        kept = [rcept_no for rcept_no in had if rcept_no not in read]   # 전에는 읽었는데 지금은 못 읽는 보고서: 그대로 둔다
        stats["전에 읽은 줄을 그대로 둔 보고서"] = len(kept)
        db.execute(delete(Product).where(Product.rcept_no.in_(list(read))))
        for rcept_no, (rows, how) in read.items():
            first = reports[rcept_no][0]
            stats[f"읽은 보고서 ({how})"] += 1
            for row_no, row in enumerate(rows, 1):
                key = (row.segment and row.segment[:100], row.name)
                std_names, std_families, named_by, unsure = named.get(key, (None, None, None, False))
                if named_by is None and (candidate(*key) is None or row.share < 0):   # 음수는 내부거래·에누리 같은 조정 줄이다
                    std_names, named_by = [], "rule"
                    stats["제품이라 할 것이 없는 줄"] += 1
                stats["담은 줄"] += 1
                stats["표준 이름이 아직 없는 줄"] += named_by is None
                db.add(Product(company_id=first.company_id, rcept_no=rcept_no, bsns_year=first.bsns_year,
                               disclosed_date=first.disclosed_date, row_no=row_no, segment=key[0], name=row.name,
                               share_pct=row.share, std_names=std_names, std_families=std_families, named_by=named_by, unsure=bool(unsure)))
        db.commit()
        stats["표를 읽은 회사"] = len(set(db.scalars(select(Product.company_id))))
        stats["사업 내용이 있는 회사"] = len({sections[0].company_id for sections in reports.values()})
    for key, value in sorted(stats.items()):
        print(f"{key}: {value:,}")


if __name__ == "__main__":
    main()
