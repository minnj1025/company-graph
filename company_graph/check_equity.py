"""지분 관계 점검. 정답지 없이 할 수 있는 세 가지를 본다.

1. 미연결 이름 중 원장의 기업인데 표기가 달라 못 붙인 것이 있는가
2. 같은 지분이 두 공시에 나온다 — 보유한 회사의 타법인 출자현황과 보유당한 회사의 최대주주 현황.
   두 공시에 적힌 숫자가 같은 값을 가리킬 수 있는가 (같은 사실을 두 번 보고한 것의 일관성 검사)
3. 어떤 시점으로 조회해도 그 뒤에 공개된 줄이 나오지 않는가 (미래 정보 누수)

실행: python -m company_graph.check_equity
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import select

from . import query
from .db import Company, CompanyAlias, Document, Relation, session
from .names import clean_reported, normalize
from .stats import rate, written_values_agree


def unlinked_candidates(db) -> list[tuple[str, str, int]]:
    """미연결 이름에서 주석 표시·영문 병기를 떼면 원장의 기업 이름과 같아지는 것."""
    aliases = dict(db.execute(select(CompanyAlias.alias, Company.name).join(Company)).all())
    counts = Counter(db.scalars(select(Relation.object_name_raw).where(
        Relation.rel_type == "equity", Relation.object_company_id.is_(None), Relation.retired_at.is_(None))))
    found = []
    for raw, n in counts.items():
        key = normalize(clean_reported(raw))
        if key in aliases:
            found.append((raw, aliases[key], n))
    return sorted(found, key=lambda x: -x[2])


def cross_check(db):
    """두 표를 (주체, 상대, 연도)로 맞춰 본다."""
    names = dict(db.execute(select(Company.company_id, Company.name)).all())
    filers = set(db.scalars(select(Document.company_id).distinct()))
    by_source: dict[str, dict[tuple, str]] = defaultdict(dict)
    for r in db.scalars(select(Relation).where(Relation.rel_type == "equity", Relation.object_company_id.is_not(None),
                                               Relation.subject_company_id.is_not(None),
                                               Relation.retired_at.is_(None))):
        by_source[r.attrs["source"]][(r.subject_company_id, r.object_company_id, r.as_of_date.year)] = r.attrs["raw_pct"]
    held, holders = by_source["other_corp_investments"], by_source["largest_shareholders"]

    # 두 회사가 다 사업보고서를 내야 양쪽 표에 나올 수 있다
    comparable = lambda k: k[0] in filers and k[1] in filers
    both = held.keys() & holders.keys()
    mismatches = [k for k in both if not written_values_agree(held[k], holders[k])]
    # 끝의 0을 자릿수로 치지 않아도 어긋나는 것만 따로 센다 ("41.10"을 41.1로 읽어도 안 맞는 경우)
    hard = [k for k in mismatches if not written_values_agree(held[k], holders[k], trailing_zeros_count=False)]
    label = lambda k: f"{names[k[0]]} → {names[k[1]]} ({k[2]})"
    return {"일치 (적힌 자릿수 그대로)": rate(len(both) - len(mismatches), len(both)),
            "일치 (끝의 0은 자릿수로 치지 않음)": rate(len(both) - len(hard), len(both)),
            "불일치 (출자현황에 적힌 값 / 최대주주 현황에 적힌 값, *는 끝의 0을 빼도 불일치)": [
                f"{'*' if k in hard else ' '} {label(k)}: {held[k]} / {holders[k]}" for k in sorted(mismatches)],
            "최대주주 현황에만 있음 (출자현황이 빠뜨림)": sorted(
                f"{label(k)} {holders[k]}%" for k in holders.keys() - held.keys() if comparable(k)),
            "출자현황에만 있음 (최대주주 현황에 안 나옴)": len([k for k in held.keys() - holders.keys() if comparable(k)]),
            "합쳐서 본 지분 쌍 (전체)": len(held.keys() | holders.keys())}


def leak_check(db) -> tuple[int, int]:
    """매달 한 번씩 시점을 옮겨 가며 수집 대상 전체를 조회해, 그 뒤에 공개된 줄이 나오는지 센다."""
    scope = list(db.scalars(select(Company.company_id).where(Company.in_scope)))
    day, checked, leaked = date(2024, 3, 31), 0, 0
    while day <= date.today():
        for edge in query.relations(db, day, company_ids=scope):
            checked += 1
            leaked += edge["disclosed_date"] > day
        day += timedelta(days=92)
    return checked, leaked


def main():
    with session() as db:
        print("== 1. 미연결 이름 중 원장의 기업으로 보이는 것 ==")
        candidates = unlinked_candidates(db)
        for raw, company, n in candidates:
            print(f"  {raw!r} → {company} ({n}줄)")
        print(f"  합계 {len(candidates)}가지 표기, {sum(n for _, _, n in candidates)}줄")

        print("== 2. 타법인 출자현황 ↔ 최대주주 현황 ==")
        for key, value in cross_check(db).items():
            if isinstance(value, list):
                print(f"  {key}: {len(value)}")
                for item in value:
                    print(f"    {item}")
            else:
                print(f"  {key}: {value}")

        print("== 3. 시점 누수 ==")
        checked, leaked = leak_check(db)
        print(f"  분기마다 시점을 옮겨 조회한 선 {checked:,}개 중 조회 시점 뒤에 공개된 것 {leaked}개")


if __name__ == "__main__":
    main()
