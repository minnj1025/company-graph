"""Agent 도구: 시점 필수, 기업은 번호로만, 빈 결과에 수집 범위, 고칠 수 있는 오류는 error 로."""
import json

from company_graph import agent_tools
from test_query import HMC, KIA, MOBIS, SUPPLIER, db  # noqa: F401  (db 는 pytest fixture)


def test_find_company_then_relations_by_id(db):
    found = agent_tools.call(db, "find_company", {"name": "기아"})
    assert [c["company_id"] for c in found["candidates"]] == [KIA]
    out = agent_tools.call(db, "get_relations", {"company_id": KIA, "as_of": "2026-06-30", "rel_type": "equity", "direction": "out"})
    assert out["total"] == 1 and out["relations"][0]["object"]["company_id"] == MOBIS
    assert out["relations"][0]["value"] == 18.1 and out["relations"][0]["disclosed_by"] == "양쪽 공시에서 확인"
    json.dumps(out, ensure_ascii=False)  # 그대로 Agent에게 보낼 수 있어야 한다


def test_as_of_changes_the_answer_and_is_required(db):
    ask = lambda when: agent_tools.call(db, "get_relations", {"company_id": SUPPLIER, "as_of": when,
                                                              "rel_type": "supply_contract", "direction": "out"})
    assert [r["value"] for r in ask("2026-07-31")["relations"]] == [100]
    assert [r["value"] for r in ask("2026-08-31")["relations"]] == [120]
    assert "as_of" in agent_tools.call(db, "get_relations", {"company_id": SUPPLIER, "rel_type": "equity", "direction": "out"})["error"]
    assert "YYYY-MM-DD" in ask("작년 말")["error"]


def test_empty_result_says_what_is_covered(db):
    out = agent_tools.call(db, "get_relations", {"company_id": MOBIS, "as_of": "2026-09-30", "rel_type": "supply_contract",
                                                 "direction": "out", "disclosed_from": "2026-01-01"})
    assert out["total"] == 0 and out["note"] and out["coverage"]["source"]


def test_filings_show_the_correction_chain(db):
    out = agent_tools.call(db, "get_filings", {"company_id": SUPPLIER, "as_of": "2026-09-30", "doc_type": "supply_contract"})
    assert [f["rcept_no"] for f in out["filings"]] == ["20260724000001", "20260805000001"]


def test_unknown_company_and_tool_are_errors_not_exceptions(db):
    assert "find_company" in agent_tools.call(db, "get_relations", {"company_id": 999, "as_of": "2026-01-01",
                                                                    "rel_type": "equity", "direction": "out"})["error"]
    assert "error" in agent_tools.call(db, "run_sql", {"sql": "select 1"})
    assert agent_tools.call(db, "find_paths", {"from_company_id": HMC, "to_company_id": MOBIS, "as_of": "2026-06-30"})["total"] >= 1


def test_superseded_filings_and_counterparty_filter(db):
    ask = lambda **more: agent_tools.call(db, "get_relations", {"company_id": SUPPLIER, "as_of": "2026-09-30",
                                                               "rel_type": "supply_contract", "direction": "out", **more})
    assert [r["value"] for r in ask()["relations"]] == [120]
    both = ask(include_superseded=True)["relations"]
    assert [(r["value"], r["superseded_on"]) for r in both] == [(100, "2026-08-05"), (120, None)]
    assert ask(counterparty_id=HMC)["total"] == 1 and ask(counterparty_id=KIA)["total"] == 0


def test_holder_not_in_master_is_listed_by_name(db):
    from datetime import date
    from decimal import Decimal
    from company_graph.db import Relation
    db.add(Relation(subject_company_id=None, subject_name_raw="홍길동", object_company_id=MOBIS, object_name_raw="현대모비스(주)",
                    rel_type="equity", value_num=Decimal("20.00"), value_unit="pct", as_of_date=date(2025, 12, 31),
                    disclosed_date=date(2026, 3, 9), rcept_no="20260309000001", extract_method="api", trust_tier=1,
                    attrs={"source": "largest_shareholders", "relation_to_filer": "본인"}))
    db.flush()
    out = agent_tools.call(db, "get_relations", {"company_id": MOBIS, "as_of": "2026-06-30", "rel_type": "equity", "direction": "in"})
    top = out["relations"][0]
    assert (top["subject"]["name"], top["subject"]["company_id"], top["value"]) == ("홍길동", None, 20.0)
    assert top["detail"]["relation_to_filer"] == "본인" and out["total"] == 2
    assert agent_tools.call(db, "find_paths", {"from_company_id": KIA, "to_company_id": MOBIS, "as_of": "2026-06-30"})["total"] >= 1


def test_find_disclosers_counts_new_and_corrected_filings(db):
    out = agent_tools.call(db, "find_disclosers", {"as_of": "2026-09-30", "rel_type": "supply_contract",
                                                   "disclosed_from": "2026-07-01", "disclosed_to": "2026-09-30"})
    assert [(c["company_id"], c["new"] + c["corrections"]) for c in out["companies"]] == [(SUPPLIER, 2), (5, 1)]
    only_kia = agent_tools.call(db, "find_disclosers", {"as_of": "2026-09-30", "rel_type": "supply_contract",
                                                        "disclosed_from": "2026-07-01", "disclosed_to": "2026-09-30",
                                                        "counterparty_id": KIA})
    assert [c["company_id"] for c in only_kia["companies"]] == [5]
    assert "industry" in agent_tools.call(db, "list_companies", {})["error"]


def test_find_by_product_by_family_and_by_keyword(db):
    from datetime import date
    from decimal import Decimal
    from company_graph.db import Product
    row = lambda company, no, segment, name, share, std, families: Product(
        company_id=company, rcept_no=f"2026031500000{company}", bsns_year=2025, disclosed_date=date(2026, 3, 15), row_no=no,
        segment=segment, name=name, share_pct=Decimal(share), std_names=std, std_families=families, named_by="test")
    db.add_all([row(SUPPLIER, 1, "LiBS", "분리막 등", "90", ["이차전지 분리막"], ["이차전지 소재"]),
                row(SUPPLIER, 2, None, "기타", "10", [], []),
                row(MOBIS, 1, "제품", "분리막", "30", ["이차전지 분리막"], ["이차전지 소재"]),
                row(MOBIS, 2, "제품", "모듈", "70", ["자동차 모듈"], ["기타 자동차 부품"])])
    db.commit()
    ask = lambda **more: agent_tools.call(db, "find_by_product", {"as_of": "2026-06-30", "listed_only": False, **more})
    out = ask(keywords=["분리 막"])
    assert [(c["company_id"], c["share_pct"], c["rows"][0]["name"]) for c in out["companies"]] == [(SUPPLIER, 90.0, "분리막 등"), (MOBIS, 30.0, "분리막")]
    assert out["family_spread"] == [{"family": "이차전지 소재", "companies": 2}]
    assert out["_graph"][0] == (SUPPLIER, 90.0, [("이차전지 분리막", "product")])
    # 제품군으로 찾으면 그 제품군의 줄만 걸리고, 그래프에는 제품군 점으로 그린다
    cars = ask(families=["기타 자동차 부품"])
    assert [(c["company_id"], c["share_pct"]) for c in cars["companies"]] == [(MOBIS, 70.0)]
    assert cars["_graph"] == [(MOBIS, 70.0, [("기타 자동차 부품", "family")])]
    assert [c["company_id"] for c in ask(keywords=["분리막"], min_share=50)["companies"]] == [SUPPLIER]
    assert ask(keywords=["분리막"], as_of="2026-01-01")["total"] == 0   # 보고서가 나오기 전 시점
    assert "목록에 없습니다" in ask(families=["반도체"])["error"]
    assert "error" in ask()
    # 상표나 약어로 사업부문 칸에만 적힌 것도 찾는다
    assert ask(keywords=["LiBS"])["total"] == 1
    products = agent_tools.call(db, "get_products", {"company_id": MOBIS, "as_of": "2026-06-30"})
    assert products["read"] and [p["share_pct"] for p in products["products"]] == [30.0, 70.0]
    assert products["products"][1]["products"] == [{"name": "자동차 모듈", "family": "기타 자동차 부품"}]
    assert agent_tools.call(db, "get_products", {"company_id": KIA, "as_of": "2026-06-30"})["read"] is False
    json.dumps({k: v for k, v in out.items() if not k.startswith("_")}, ensure_ascii=False)


def test_resale_rows_are_marked():
    from types import SimpleNamespace as Row
    from company_graph.agent_tools import _resale

    assert _resale(Row(segment="상품", name="면류", std_names=["파스타"]))
    assert _resale(Row(segment=None, name="MLCC", std_names=["전자부품 유통"]))
    assert not _resale(Row(segment="제품", name="라면", std_names=["라면"]))
    assert not _resale(Row(segment="제/상품", name="음료", std_names=["음료"]))
    assert not _resale(Row(segment="금융상품 판매", name="펀드", std_names=["펀드 판매"]))
