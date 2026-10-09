"""평가 3판(무엇을 파는 회사인가)의 문항 묶음을 만든다.

2판은 공시된 계약과 지분을 물었다. 3판은 사업보고서의 "사업의 내용"에서 나오는 질문을 묻는다:
어떤 제품을 누가 파는가, 한 회사가 무엇으로 돈을 버는가, 어떤 이슈에 닿는 회사는 어디인가.

묶음에는 보고서에 적힌 것만 담는다: 제품 표의 줄(사업부문, 품목 이름, 매출 비중)과 사업 내용의 글 조각.
이 프로젝트가 붙인 표준 이름, 제품군, 분류 코드는 넣지 않는다 (그것이 맞는지를 재는 시험이라서).

실행: python -m eval.v3.packets   → eval/v3/packets/*.json
"""
import json
import re
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from company_graph.db import BusinessSection, Company, Product, session

ROOT = Path(__file__).parent
AS_OF = date(2026, 10, 8)
LISTED = ("Y", "K")
SNIPPET = 110        # 낱말 앞뒤로 보여 줄 글자 수
MAX_COMPANIES = 60   # 글에서 찾은 회사는 많이 나온 순서로 여기까지

# (번호, 종류, 주제, 찾을 낱말들). 낱말은 후보를 넓게 모으는 데만 쓴다. 누가 정말 파는지는 쓰는 사람이 읽고 가린다
TOPICS = [
    ("P01", "product", "이차전지 분리막", ["분리막", "LiBS", "세퍼레이터"]),
    ("P02", "product", "라면", ["라면", "면류", "봉지면", "용기면"]),
    ("P03", "product", "치과용 임플란트", ["임플란트"]),
    ("P04", "product", "보툴리눔 톡신", ["보툴리눔", "톡신", "보톡스"]),
    ("P05", "product", "적층세라믹콘덴서(MLCC)", ["MLCC", "적층세라믹"]),
    ("P06", "product", "타이어", ["타이어"]),
    ("P07", "product", "이차전지 양극재", ["양극재", "양극활물질"]),
    ("P08", "product", "동박", ["동박", "전지박"]),
    ("P09", "product", "시멘트", ["시멘트"]),
    ("P10", "product", "콘택트렌즈", ["콘택트렌즈", "컨택트렌즈"]),
    ("I01", "issue", "원자력발전소 건설·수출", ["원전", "원자력"]),
    ("I02", "issue", "방위산업 수출", ["방산", "방위산업"]),
    ("I03", "issue", "화장품 위탁생산(ODM·OEM)", ["ODM", "OEM"]),
]
ISSUE_FILTER = {"I03": "화장품"}   # 낱말이 너무 넓은 주제는 이 말이 같이 있는 글만 본다
# (번호, 종목코드): 한 회사가 무엇으로 돈을 버는가
COMPANIES = [("C01", "004370"), ("C02", "271560"), ("C03", "128940"), ("C04", "251270"), ("C05", "003490"), ("C06", "004020")]


def latest_rows(db, company_ids=None):
    latest = select(Product.company_id, func.max(Product.rcept_no).label("rcept_no")).where(Product.disclosed_date <= AS_OF).group_by(Product.company_id).subquery()
    query = select(Product).join(latest, (Product.company_id == latest.c.company_id) & (Product.rcept_no == latest.c.rcept_no))
    if company_ids is not None:
        query = query.where(Product.company_id.in_(company_ids))
    return list(db.scalars(query.order_by(Product.company_id, Product.row_no)))


def latest_sections(db):
    latest = select(BusinessSection.company_id, func.max(BusinessSection.rcept_no).label("rcept_no")).where(BusinessSection.disclosed_date <= AS_OF).group_by(BusinessSection.company_id).subquery()
    return db.scalars(select(BusinessSection).join(latest, (BusinessSection.company_id == latest.c.company_id) & (BusinessSection.rcept_no == latest.c.rcept_no)))


def row_json(row: Product) -> dict:
    return {"segment": row.segment, "name": row.name, "share_pct": float(row.share_pct)}


def main():
    out = ROOT / "packets"
    out.mkdir(parents=True, exist_ok=True)
    with session() as db:
        listed = {c.company_id: c for c in db.scalars(select(Company).where(Company.corp_cls.in_(LISTED)))}
        rows = [r for r in latest_rows(db) if r.company_id in listed]
        by_company: dict[int, list[Product]] = {}
        for row in rows:
            by_company.setdefault(row.company_id, []).append(row)
        sections = [s for s in latest_sections(db) if s.company_id in listed]
        brief = lambda c: {"name": c.label, "legal_name": c.name, "stock_code": c.stock_code, "market": {"Y": "유가증권", "K": "코스닥"}[c.corp_cls]}

        for number, kind, topic, words in TOPICS:
            pattern = re.compile("|".join(map(re.escape, words)), re.IGNORECASE)
            must = ISSUE_FILTER.get(number)
            # 1. 제품 표에 그 낱말이 적힌 회사: 표 전체를 보여 준다 (다른 줄과 견주어 주력인지 볼 수 있게)
            in_table = {cid for cid, items in by_company.items() if any(pattern.search(f"{r.segment or ''} {r.name}") for r in items)}
            # 2. 사업 내용의 글에 그 낱말이 나온 회사: 낱말 둘레의 글 조각
            mentions: dict[int, list[str]] = {}
            counts: dict[int, int] = {}
            for section in sections:
                found = list(pattern.finditer(section.text))
                if must:
                    found = [m for m in found if must in section.text[max(0, m.start() - 200):m.end() + 200]]
                if not found:
                    continue
                counts[section.company_id] = counts.get(section.company_id, 0) + len(found)
                for match in found[:2]:
                    if len(mentions.setdefault(section.company_id, [])) < 4:
                        mentions[section.company_id].append(f"[{section.title}] …" + " ".join(section.text[max(0, match.start() - SNIPPET):match.end() + SNIPPET].split()) + "…")
            if must:
                in_table = {cid for cid in in_table if cid in counts}
            ranked = sorted(counts, key=lambda cid: (cid not in in_table, -counts[cid]))[:MAX_COMPANIES]
            companies = []
            for cid in sorted(in_table | set(ranked), key=lambda cid: (cid not in in_table, -counts.get(cid, 0))):
                items = by_company.get(cid, [])
                companies.append({**brief(listed[cid]),
                                  "report": items[0].rcept_no if items else None,
                                  "product_table": [row_json(r) for r in items] if cid in in_table else None,
                                  "product_table_note": None if cid in in_table else ("제품 표에는 이 낱말이 없다" if items else "제품 표를 읽지 못한 회사"),
                                  "mentions_in_business_text": counts.get(cid, 0),
                                  "snippets": mentions.get(cid, [])})
            packet = {"id": number, "type": kind, "topic": topic, "search_words": words, "as_of": AS_OF.isoformat(),
                      "note": "search_words 는 후보를 모으는 데만 썼다. 이 낱말이 나온다고 그 제품을 파는 회사는 아니다(장비·소재·고객사일 수 있다).",
                      "companies_with_word_in_product_table": len(in_table), "companies_with_word_in_text": len(counts),
                      "companies": companies}
            (out / f"{number}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
            print(number, topic, "표", len(in_table), "글", len(counts), "묶음에", len(companies))

        for number, code in COMPANIES:
            company = next(c for c in listed.values() if c.stock_code == code)
            items = by_company.get(company.company_id, [])
            texts = [s for s in sections if s.company_id == company.company_id and s.section_no in (1, 2)]
            packet = {"id": number, "type": "company", "as_of": AS_OF.isoformat(), "company": brief(company),
                      "report": items[0].rcept_no if items else None,
                      "product_table": [row_json(r) for r in items],
                      "business_text": [{"title": s.title, "text": s.text[:6000]} for s in texts]}
            (out / f"{number}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
            print(number, company.label, "표", len(items), "줄")


if __name__ == "__main__":
    main()
