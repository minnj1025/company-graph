"""지분 보강: 사업보고서 본문의 "타법인출자 현황(상세)"에서 회사가 표준 표 밖에 따로 붙인 출자 표를 넣는다.

API(extract_equity)가 주는 줄은 표준 표의 줄뿐이다. 여기서는 그 밖의 줄만 더한다. 원문은 계열회사 표를 읽을 때 받아 둔 캐시를 쓴다.
실행: python -m company_graph.extract_invest_detail [연도 ...]
"""
import sys
from collections import Counter
from datetime import date, datetime

from sqlalchemy import select

from . import dart, loader
from .db import Company, Document, Relation, init_db, session
from .extract_equity import alias_index, link_in_context, own_affiliates, to_decimal
from .invest_detail_parser import parse
from .names import clean_reported, normalize

VERSION = "invest-detail-1"
SOURCE = "investment_detail_table"


def main(years: list[int]):
    engine = init_db()
    stats = Counter()
    with session(engine) as db:
        index = alias_index(db)
        docs = db.scalars(select(Document).where(Document.doc_type == "annual", Document.bsns_year.in_(years))
                          .order_by(Document.rcept_no)).all()
        for doc in docs:
            stats["사업보고서"] += 1
            if stats["사업보고서"] % 1000 == 0:
                print(f"  {stats['사업보고서']}/{len(docs)}건", flush=True)
            try:
                detail = parse(dart.document(doc.rcept_no))
            except dart.DailyBudgetExceeded as stop:
                print(f"중단: {stop}")
                break
            except dart.DartError:
                stats["원문을 받지 못함"] += 1
                continue
            scope = [Relation.rcept_no == doc.rcept_no, Relation.rel_type == "equity",
                     Relation.attrs["source"].as_string() == SOURCE]
            if detail is None:
                stats["상세 장 없음"] += 1
                continue
            stats["칸 수가 달라 읽지 않은 줄"] += detail.odd_rows
            if detail.holdings:
                stats["따로 붙인 표가 있는 사업보고서"] += 1
            filer = db.get(Company, doc.company_id)
            affiliates = own_affiliates(db, filer)
            # 표준 표(API)에 이미 있는 이름은 다시 넣지 않는다
            known = {normalize(clean_reported(name)) for name in db.scalars(select(Relation.object_name_raw).where(
                Relation.rcept_no == doc.rcept_no, Relation.rel_type == "equity", Relation.retired_at.is_(None),
                Relation.subject_company_id == filer.company_id, Relation.attrs["source"].as_string() == "other_corp_investments"))}
            new_rows, seen = [], set()
            for holding in detail.holdings:
                key = normalize(clean_reported(holding.name))
                raw_pct = holding.pct.replace("%", "").strip()
                pct = to_decimal(raw_pct)
                if key in known or key in seen:
                    stats["표준 표에 이미 있거나 겹치는 줄"] += 1
                    continue
                if pct is None or not 0 <= pct <= 100:
                    stats["기말 지분율이 없거나 범위 밖"] += 1
                    continue
                seen.add(key)
                object_id, reason = link_in_context(index, affiliates, holding.name)
                if object_id == filer.company_id:
                    continue
                stats[f"상대를 연결 ({reason})" if object_id else "상대가 원장에 없음"] += 1
                new_rows.append(Relation(
                    subject_company_id=filer.company_id, object_company_id=object_id, object_name_raw=holding.name[:300],
                    rel_type="equity", value_num=pct, value_unit="pct",
                    as_of_date=detail.as_of or date(doc.bsns_year, 12, 31), disclosed_date=doc.rcept_dt,
                    rcept_no=doc.rcept_no, extract_method="rule", trust_tier=1,
                    attrs={"source": SOURCE, "link_reason": reason, "raw_pct": raw_pct, "purpose": holding.purpose,
                           "listed": holding.listed, "shares": holding.shares, "book_value": holding.book_value}))
            stats.update({f"줄: {k}": v for k, v in loader.sync(db, scope, new_rows, VERSION).items()})
            db.commit()
    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [2023, 2024, 2025])
