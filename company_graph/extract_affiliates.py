"""4단계: 계열 관계. 사업보고서 원문의 "계열회사 현황(상세)" 표를 읽어 relation 표에 넣는다.

주체 = 보고서를 낸 회사, 상대 = 표에 적힌 계열회사(자기 자신 제외), 기준일 = 표의 기준일.
상대는 법인등록번호로 원장에 붙인다. 원장에 없는 회사는 이름과 법인등록번호로 새로 넣는다
(비상장 계열사는 DART 고유번호가 없어도 그래프의 점이 되어야 한다).
공정위 명단은 여기서 쓰지 않는다. 채점용이다 (check_affiliates.py).

실행: python -m company_graph.extract_affiliates [연도 ...]   (연도를 안 주면 2023~2025 전부)
호출 수: 사업보고서 1건당 원문 1건(5~10MB). 지금 대상은 약 370건.
전체 상장사로 넓히면 약 2,650 × 3 = 8,000건, 용량은 수십 GB라 원문을 지우면서 받아야 한다.
"""
import sys
from collections import Counter
from datetime import datetime

from sqlalchemy import delete, select

from . import dart, loader
from .affiliate_parser import parse
from .db import Company, CompanyAlias, Document, QualityLog, Relation, init_db, session
from .names import normalize


VERSION = "affiliate-3"  # 3: 법인등록번호 오타로 갈라진 회사를 하나로. 2: 줄을 지우지 않음


def jurir_checks(jurir_no: str | None) -> bool:
    """법인등록번호 13자리의 마지막 검증 숫자가 맞는가. DART에 등록된 번호도 2%쯤은 맞지 않으니 참고로만 쓴다."""
    if not (jurir_no and len(jurir_no) == 13 and jurir_no.isdigit()):
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 2) for i, d in enumerate(jurir_no[:12]))
    return (10 - total % 10) % 10 == int(jurir_no[12])


def typo_duplicates(db) -> dict[int, int]:
    """계열회사 표에 법인등록번호를 한두 자리 틀리게 적은 탓에 따로 만들어진 회사 → 원래 회사.

    이름(정규화)이 같고 번호가 두 자리 이하로 다르면 같은 회사로 본다. 남길 쪽은 DART에 등록된 회사, 검증 숫자가 맞는 번호,
    먼저 만든 회사 순으로 고른다. 내려진 쪽의 별칭은 지워 이름 연결에 끼어들지 않게 한다 (회사 줄은 옛 관계가 가리키므로 둔다).
    """
    by_name: dict[str, list[Company]] = {}
    for company in db.scalars(select(Company).where(Company.jurir_no.is_not(None))):
        if len(company.jurir_no) == 13:
            by_name.setdefault(normalize(company.name), []).append(company)
    merged: dict[int, int] = {}
    for group in by_name.values():
        group.sort(key=lambda c: (c.corp_code is None, not jurir_checks(c.jurir_no), c.company_id))
        for keep_at, keep in enumerate(group):
            if keep.company_id in merged:
                continue
            for other in group[keep_at + 1:]:
                if other.company_id not in merged and other.corp_code is None \
                        and sum(a != b for a, b in zip(keep.jurir_no, other.jurir_no)) <= 2:
                    merged[other.company_id] = keep.company_id
    if merged:
        db.execute(delete(CompanyAlias).where(CompanyAlias.company_id.in_(list(merged))))
    return merged


def company_by_jurir(db, cache: dict[str, Company], jurir_no: str, name: str, merged: dict[int, int]) -> Company:
    """법인등록번호로 원장에서 찾고, 없으면 새로 넣는다. 번호를 틀리게 적은 것으로 보이면 원래 회사를 돌려준다."""
    if jurir_no not in cache:
        company = db.scalar(select(Company).where(Company.jurir_no == jurir_no))
        if company is not None and company.company_id in merged:
            company = db.get(Company, merged[company.company_id])
        if company is None:
            twin = [c for c in db.scalars(select(Company).join(CompanyAlias).where(CompanyAlias.alias == normalize(name)).distinct())
                    if c.jurir_no and len(c.jurir_no) == 13 and sum(a != b for a, b in zip(c.jurir_no, jurir_no)) <= 2]
            company = twin[0] if len(twin) == 1 else None
        if company is None:
            company = Company(jurir_no=jurir_no, name=name, in_scope=False)
            db.add(company)
            db.flush()
            db.add(CompanyAlias(alias=normalize(name), company_id=company.company_id, source="auto"))
        cache[jurir_no] = company
    return cache[jurir_no]


def main(years: list[int]):
    engine = init_db()
    stats = Counter()
    with session(engine) as db:
        by_jurir: dict[str, Company] = {}
        merged = typo_duplicates(db)
        stats["법인등록번호 오타로 따로 만들어졌던 회사"] = len(merged)
        docs = db.scalars(select(Document).where(Document.doc_type == "annual", Document.bsns_year.in_(years))
                          .order_by(Document.rcept_no)).all()
        for doc in docs:
            filer = db.get(Company, doc.company_id)
            stats["사업보고서"] += 1
            if stats["사업보고서"] % 500 == 0:
                print(f"  {stats['사업보고서']}/{len(docs)}건, 오늘 DART 호출 {dart.calls_today()}건", flush=True)
            try:
                table = parse(dart.document(doc.rcept_no))
            except dart.DailyBudgetExceeded as stop:
                print(f"중단: {stop}. 받은 것은 캐시에 있으니 내일 같은 명령으로 이어 받는다")
                break
            except dart.DartError as error:
                stats["원문을 받지 못함"] += 1
                print(f"  {filer.name}: {error}")
                continue
            db.execute(delete(QualityLog).where(QualityLog.rcept_no == doc.rcept_no,
                                                QualityLog.detail.like("계열회사 표%")))
            scope = [Relation.rcept_no == doc.rcept_no, Relation.rel_type == "affiliate"]
            if table is None:
                stats["계열회사 표 없음"] += 1
                loader.sync(db, scope, [], VERSION)
                db.commit()
                continue
            stats["계열회사 표를 읽음"] += 1
            if table.count_matches is False:
                stats["읽은 줄 수가 표의 회사수와 다름"] += 1
                db.add(QualityLog(check_name="cross_check", rcept_no=doc.rcept_no, created_at=datetime.now(),
                                  detail=f"계열회사 표: {filer.name} 회사수 {table.declared_count}, "
                                         f"읽은 줄 {len(table.affiliates)}"))
            elif table.count_matches:
                stats["읽은 줄 수가 표의 회사수와 같음"] += 1
            new_rows = []
            for affiliate in table.affiliates:
                if affiliate.jurir_no and affiliate.jurir_no == filer.jurir_no:
                    continue  # 자기 자신
                target = company_by_jurir(db, by_jurir, affiliate.jurir_no, affiliate.name, merged) if affiliate.jurir_no else None
                stats["계열 관계"] += 1
                stats["상대를 법인등록번호로 연결" if target else "상대에 법인등록번호 없음(해외 등)"] += 1
                new_rows.append(Relation(
                    subject_company_id=filer.company_id, object_company_id=target and target.company_id,
                    object_name_raw=affiliate.name[:300], rel_type="affiliate",
                    as_of_date=table.as_of, disclosed_date=doc.rcept_dt, rcept_no=doc.rcept_no,
                    extract_method="rule", trust_tier=1,
                    attrs={"listed": affiliate.listed, "jurir_no": affiliate.jurir_no}))
            stats.update({f"줄: {k}": v for k, v in loader.sync(db, scope, new_rows, VERSION).items()})
            db.commit()
    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")
    print(f"오늘 DART 호출 {dart.calls_today()}건")


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [2023, 2024, 2025])
