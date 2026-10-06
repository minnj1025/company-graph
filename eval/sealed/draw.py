"""봉인 시험 문항에 쓸 기업과 공시를 무작위로 뽑는다.

뽑는 모집단은 DB의 관계가 아니라 공시 목록(document 표)과 기업 원장이다. 관계에서 뽑으면 추출기가 읽지 못한 공시가
처음부터 빠져서, 시험이 추출기의 실수를 잡지 못한다. 씨앗을 고정해 같은 명령이면 같은 결과가 나온다.

실행: python -m eval.sealed.draw            (결과는 화면에. 정답 초안은 사람이 원문을 보고 확정한다)
"""
import random

from sqlalchemy import select, text

from company_graph.db import Company, Document, Relation, session

SEED = 20261006
LISTED = ("Y", "K", "N")
LINK = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


def show(db, title: str, docs: list[Document]):
    print(f"\n## {title}")
    for doc in docs:
        company = db.get(Company, doc.company_id)
        print(f"- {company.name} ({company.stock_code}) | {doc.rcept_dt} | {doc.report_nm} | {LINK}{doc.rcept_no}")
        for r in db.scalars(select(Relation).where(Relation.rcept_no == doc.rcept_no, Relation.retired_at.is_(None))):
            print(f"    DB 초안: {r.rel_type} → {r.object_name_raw} | {r.value_num} {r.value_unit or ''} | 기준일 {r.as_of_date}"
                  f" | 무효일 {r.invalidated_date} | {(r.attrs or {}).get('title') or (r.attrs or {}).get('purpose') or ''}")


def main():
    rng = random.Random(SEED)
    with session() as db:
        listed = set(db.scalars(select(Company.company_id).where(Company.corp_cls.in_(LISTED))))
        docs = [d for d in db.scalars(select(Document).where(Document.doc_type.in_(("supply_contract", "event")))
                                      .order_by(Document.rcept_no)) if d.company_id in listed]
        supply = [d for d in docs if d.doc_type == "supply_contract"]
        stakes = [d for d in docs if d.doc_type == "event"]
        corrected = {d.corrects_rcept_no for d in docs if d.corrects_rcept_no}

        def pick(pool: list[Document], n: int) -> list[Document]:
            """회사가 겹치지 않게 n건."""
            chosen, seen = [], set()
            for doc in rng.sample(pool, len(pool)):
                if doc.company_id not in seen:
                    chosen.append(doc)
                    seen.add(doc.company_id)
                if len(chosen) == n:
                    break
            return chosen

        show(db, "A. 공급계약 단건 (3) — 정정이 없는 최초 공시",
             pick([d for d in supply if not d.is_correction and "해지" not in d.report_nm and d.rcept_no not in corrected], 3))

        by_company: dict[int, list[Document]] = {}
        for d in supply:
            if d.rcept_dt.year == 2025 and not d.is_correction and "해지" not in d.report_nm:
                by_company.setdefault(d.company_id, []).append(d)
        several = sorted(c for c, ds in by_company.items() if 3 <= len(ds) <= 8)
        for company_id in rng.sample(several, 2):
            show(db, "B. 공급계약 목록 (2 중 하나) — 2025년에 최초 공시가 3~8건인 회사", by_company[company_id])

        for doc in pick([d for d in supply if d.is_correction and d.corrects_rcept_no], 2):
            show(db, "C. 시점 쌍 (2쌍 중 하나) — 정정 전 시점과 정정 후 시점에 같은 질문", [db.get(Document, doc.corrects_rcept_no), doc])

        show(db, "C. 해지 (1)", pick([d for d in supply if "해지" in d.report_nm], 1))
        show(db, "C. 철회 (1)", pick([d for d in stakes if "철회" in d.report_nm], 1))
        show(db, "D. 취득·처분 결정 단건 (3)",
             pick([d for d in stakes if not d.is_correction and d.rcept_no not in corrected], 3))

        by_company = {}
        for d in stakes:
            if not d.is_correction:
                by_company.setdefault(d.company_id, []).append(d)
        show(db, "D. 취득·처분 결정 목록 (1) — 2024년 이후 최초 공시가 3~6건인 회사",
             by_company[rng.choice(sorted(c for c, ds in by_company.items() if 3 <= len(ds) <= 6))])

        filers = {d.company_id for d in supply}
        none = rng.sample(sorted(listed - filers), 2)
        print("\n## H. 공시가 없는 경우 (2) — 2024년 이후 공급계약 공시가 한 건도 없는 상장사")
        for company_id in none:
            company = db.get(Company, company_id)
            print(f"- {company.name} ({company.stock_code})")
        hidden_nos = set(db.scalars(text("select rcept_no from relation where rel_type='supply_contract' and retired_at is null "
                                         "and json_extract(attrs, '$.party_hidden') = true")))
        show(db, "H. 상대를 가린 공시 (1)", pick([d for d in supply if not d.is_correction and d.rcept_no in hidden_nos], 1))

        pool = sorted(listed)
        print("\n## E·F·G. 지분, 계열, 두 단계에 쓸 상장사 (수집이 끝난 뒤 정답 초안을 만든다)")
        for company_id in rng.sample(pool, 12):
            company = db.get(Company, company_id)
            print(f"- {company.name} ({company.stock_code}, {company.corp_cls})")


if __name__ == "__main__":
    main()
