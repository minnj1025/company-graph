"""1단계: 기업 원장(company 표)을 만든다.

원장에는 종목코드가 있는 회사 전부와 대상 기업집단의 소속회사를 넣는다.
그중 수집 대상은 in_scope 로 표시한다. 대상을 넓힐 때는 scope_rule 만 바꾼다.
대상 밖 회사도 원장에 두는 이유는 공시에 적힌 상대 이름을 붙일 곳이 있어야 하기 때문이다.

실행: python -m company_graph.load_companies
"""
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from . import dart, ftc
from .cache import cached_json
from .config import FTC_DESIGNATION_YM, SCOPE_GROUP, SCOPE_INDUTY_PREFIX
from .db import Company, CompanyAlias, init_db, session
from .manual_aliases import SINGLE
from .names import normalize


def scope_rule(info: dict, ftc_group: str | None) -> bool:
    """수집 대상인가. 1단계: 현대자동차그룹 소속이거나, 자동차 업종의 유가·코스닥 상장사 (DESIGN.md 3장)."""
    listed = info.get("corp_cls") in ("Y", "K")
    return ftc_group == SCOPE_GROUP or (listed and (info.get("induty_code") or "").startswith(SCOPE_INDUTY_PREFIX))


def find_unlisted(affiliates: list[dict], corp_codes: list[dict], known_jurir: set[str]) -> dict[str, dict]:
    """비상장 계열사의 DART 고유번호를 찾는다. 이름으로 후보를 고르고 법인등록번호로 확정한다."""
    by_name: dict[str, list[str]] = {}
    for c in corp_codes:
        by_name.setdefault(normalize(c["corp_name"]), []).append(c["corp_code"])
    found = {}
    for row in affiliates:
        if row["jurirno"] in known_jurir:
            continue
        for corp_code in by_name.get(normalize(row["entrprsNm"]), []):
            info = dart.company(corp_code)
            if info.get("jurir_no") == row["jurirno"]:
                found[row["jurirno"]] = info
                break
    return found


def main():
    corp_codes = cached_json("dart", "corp_codes", dart.corp_codes)
    ftc_rows = cached_json("ftc_affiliates", FTC_DESIGNATION_YM, lambda: ftc.affiliates(FTC_DESIGNATION_YM))
    group_of = {r["jurirno"]: r["unityGrupNm"] for r in ftc_rows}

    # 종목코드가 있는 회사 전부. 처음에는 약 4,000건을 받고, 그 뒤로는 새로 생긴 회사만 받는다
    with ThreadPoolExecutor(4) as pool:
        infos = list(pool.map(dart.company, [c["corp_code"] for c in corp_codes if c.get("stock_code")]))
    by_jurir = {i["jurir_no"]: i for i in infos if i.get("jurir_no")}
    group_rows = [r for r in ftc_rows if r["unityGrupNm"] == SCOPE_GROUP]
    unlisted = find_unlisted(group_rows, corp_codes, set(by_jurir))
    not_in_dart = [r for r in group_rows if r["jurirno"] not in by_jurir and r["jurirno"] not in unlisted]

    engine = init_db()
    with session(engine) as db:
        existing = {c.corp_code: c for c in db.scalars(select(Company).where(Company.corp_code.is_not(None)))}
        existing_jurir = {c.jurir_no: c for c in db.scalars(select(Company).where(Company.corp_code.is_(None)))}
        aliases = set(db.execute(select(CompanyAlias.alias, CompanyAlias.company_id)).all())

        def save(*, corp_code, jurir_no, names: list[str], **fields):
            company = existing.get(corp_code) if corp_code else existing_jurir.get(jurir_no)
            if company is None and corp_code and jurir_no in existing_jurir:
                company = existing_jurir.pop(jurir_no)  # 전에는 DART 번호를 못 찾았던 회사
            if company is None:
                company = Company()
                db.add(company)
            company.corp_code, company.jurir_no = corp_code, jurir_no
            for key, value in fields.items():
                setattr(company, key, value)
            db.flush()
            # alias 칸에는 names.normalize()를 거친 꼴을 넣는다. 조회할 때도 같은 함수를 거친다
            for key in {normalize(n) for n in names if n} - {""}:
                if (key, company.company_id) not in aliases:
                    db.add(CompanyAlias(alias=key, company_id=company.company_id, source="auto"))
                    aliases.add((key, company.company_id))

        for info in list(infos) + list(unlisted.values()):
            group = group_of.get(info.get("jurir_no"))
            save(corp_code=info["corp_code"], jurir_no=info.get("jurir_no") or None,
                 names=[info["corp_name"], info.get("stock_name")], biz_no=info.get("bizr_no") or None,
                 stock_code=info.get("stock_code") or None, name=info["corp_name"], corp_cls=info.get("corp_cls"),
                 induty_code=info.get("induty_code"), ftc_group=group, in_scope=scope_rule(info, group))
        for row in not_in_dart:
            save(corp_code=None, jurir_no=row["jurirno"], names=[row["entrprsNm"]], biz_no=row.get("bizrno") or None,
                 name=row["entrprsNm"], induty_code=row.get("indutyCode"), ftc_group=SCOPE_GROUP, in_scope=True)
        for alias, stock_code in SINGLE.items():
            target = db.scalar(select(Company).where(Company.stock_code == stock_code, Company.corp_cls.in_(("Y", "K"))))
            if target and (normalize(alias), target.company_id) not in aliases:
                db.add(CompanyAlias(alias=normalize(alias), company_id=target.company_id, source="manual"))
        db.commit()

        count = lambda *where: db.scalar(select(func.count()).select_from(Company).where(*where))
        print(f"company {count()}곳 — 수집 대상 {count(Company.in_scope)}곳 "
              f"(그중 {SCOPE_GROUP}그룹 {count(Company.in_scope, Company.ftc_group == SCOPE_GROUP)}곳), "
              f"DART에서 못 찾은 계열사 {len(not_in_dart)}곳")
        print(f"company_alias {db.scalar(select(func.count()).select_from(CompanyAlias))}줄")
    print(f"오늘 DART 호출 {dart.calls_today()}건")


if __name__ == "__main__":
    main()
