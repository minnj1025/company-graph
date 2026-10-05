"""지분 관계 점검. 정답지 없이 할 수 있는 두 가지를 본다.

1. 미연결 이름 중 대상 기업인데 표기가 달라 못 붙인 것이 있는가
2. 같은 지분이 두 공시에 나온다 — 보유한 회사의 타법인 출자현황(relation에 넣은 값)과
   보유당한 회사의 최대주주 현황. 둘이 일치하는가

실행: python -m company_graph.check_equity
"""
from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import select

from .db import Company, CompanyAlias, Document, Relation, session
from .names import clean_reported, normalize



def tolerance(a: Decimal, b: Decimal) -> Decimal:
    """회사마다 지분율을 정수, 소수 첫째, 둘째 자리까지 다르게 적는다. 더 거칠게 적힌 쪽의 반올림 폭만큼 허용한다."""
    def decimals(x: Decimal) -> int:
        return max(0, -x.normalize().as_tuple().exponent)
    return Decimal(5) / Decimal(10) ** (min(decimals(a), decimals(b)) + 1) + Decimal("0.005")


def unlinked_candidates(db) -> list[tuple[str, str, int]]:
    """미연결 이름에서 주석 표시·영문 병기를 떼면 대상 기업 이름과 같아지는 것."""
    aliases = dict(db.execute(select(CompanyAlias.alias, Company.name).join(Company)).all())
    counts = Counter(db.scalars(select(Relation.object_name_raw).where(
        Relation.rel_type == "equity", Relation.object_company_id.is_(None))))
    found = []
    for raw, n in counts.items():
        key = normalize(clean_reported(raw))
        if key in aliases:
            found.append((raw, aliases[key], n))
    return sorted(found, key=lambda x: -x[2])


def cross_check(db):
    """relation 에 넣은 두 출처를 (주체, 상대, 연도)로 맞춰 본다."""
    names = dict(db.execute(select(Company.company_id, Company.name)).all())
    filers = set(db.scalars(select(Document.company_id).distinct()))
    by_source: dict[str, dict[tuple, Decimal]] = defaultdict(dict)
    for r in db.scalars(select(Relation).where(Relation.rel_type == "equity", Relation.object_company_id.is_not(None))):
        by_source[r.attrs["source"]][(r.subject_company_id, r.object_company_id, r.as_of_date.year)] = r.value_num
    held, holders = by_source["other_corp_investments"], by_source["largest_shareholders"]

    # 두 회사가 다 사업보고서를 내야 양쪽 표에 나올 수 있다
    comparable = lambda k: k[0] in filers and k[1] in filers
    both = held.keys() & holders.keys()
    mismatches = [(k, held[k], holders[k]) for k in both if abs(held[k] - holders[k]) > tolerance(held[k], holders[k])]
    label = lambda k: f"{names[k[0]]} → {names[k[1]]} ({k[2]})"
    return {"양쪽에 다 나온 쌍": len(both), "일치": len(both) - len(mismatches),
            "불일치": [(label(k), float(a), float(b)) for k, a, b in sorted(mismatches)],
            "최대주주 현황에만 있음 (출자현황이 빠뜨림)": sorted(
                f"{label(k)} {holders[k]}%" for k in holders.keys() - held.keys() if comparable(k)),
            "출자현황에만 있음 (최대주주 현황에 안 나옴)": len([k for k in held.keys() - holders.keys() if comparable(k)]),
            "합쳐서 본 지분 쌍 (전체)": len(held.keys() | holders.keys())}


def main():
    with session() as db:
        print("== 1. 미연결 이름 중 대상 기업으로 보이는 것 ==")
        candidates = unlinked_candidates(db)
        for raw, company, n in candidates:
            print(f"  {raw!r} → {company} ({n}줄)")
        print(f"  합계 {len(candidates)}가지 표기, {sum(n for _, _, n in candidates)}줄")

        print("== 2. 타법인 출자현황 ↔ 최대주주 현황 ==")
        result = cross_check(db)
        for key, value in result.items():
            if isinstance(value, list):
                print(f"  {key}: {len(value)}")
                for item in value:
                    print(f"    {item}")
            else:
                print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
