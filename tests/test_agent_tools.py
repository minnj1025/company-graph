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


def test_price_rows_are_parsed():
    from datetime import date
    from decimal import Decimal
    from company_graph.prices import parse

    rows = parse([{"BAS_DD": "20261008", "ISU_CD": "005930", "TDD_CLSPRC": "71,200", "FLUC_RT": "-1.25", "ACC_TRDVOL": "12,345",
                   "ACC_TRDVAL": "878,964,000", "MKTCAP": "425,000,000,000,000"},
                  {"BAS_DD": "20261008", "ISU_CD": "000001", "TDD_CLSPRC": "-"}])
    assert rows == [{"stock_code": "005930", "trade_date": date(2026, 10, 8), "close_price": Decimal("71200"), "change_pct": Decimal("-1.25"),
                     "volume": 12345, "trade_value": 878964000, "market_cap": 425000000000000}]


def _links(holders=None, rel=None):
    from company_graph.hot import Links
    return Links({i: f"회사{i}" for i in range(1, 40)}, holders or {}, rel or {})


def test_hot_product_group_needs_three_strong_members():
    from company_graph.hot import find_groups

    links = _links({"강관": {1, 2, 3, 4, 5, 6}})
    liquid = set(range(1, 40))
    quiet = {i: 0.0 for i in liquid}
    clear = find_groups({**quiet, 1: 30.0, 2: 19.0, 3: 9.4, 4: 9.1}, 3.0, liquid, links)
    assert [(g["grade"], g["why"], len(g["members"]), g["of"]) for g in clear] == [("뚜렷함", ["강관"], 4, 6)]
    # 한 곳만 급등하고 나머지는 기준선을 겨우 넘었다
    assert find_groups({**quiet, 1: 30.0, 2: 4.9, 3: 3.7}, 3.0, liquid, links) == []
    # 세 곳이 올랐지만 그 제품을 가진 곳의 절반이 안 된다
    wide = _links({"레미콘": set(range(1, 9))})
    assert find_groups({**quiet, 1: 13.0, 2: 8.0, 3: 7.0}, 3.0, liquid, wide) == []


def test_hot_relation_group_skips_hubs_and_merges_same_members():
    from company_graph.hot import find_groups

    liquid = set(range(1, 40))
    quiet = {i: 0.0 for i in liquid}
    family = {(1, 2): {"계열"}, (2, 3): {"계열", "지분"}}
    groups = find_groups({**quiet, 1: 30.0, 2: 29.0, 3: 25.0}, 3.0, liquid, _links({"MLCC": {1, 2, 3}}, family))
    assert [(g["grade"], g["kind"], sorted(g["members"])) for g in groups] == [("보통", "둘 다", [1, 2, 3])]
    # 여러 곳에 출자한 회사(20)를 거쳐서만 이어진 곳들은 무리가 아니다
    hub = {(i, 20): {"지분"} for i in range(1, 13)}
    assert find_groups({**quiet, 1: 9.0, 2: 9.0, 3: 9.0, 20: 9.0}, 3.0, liquid, _links(rel=hub)) == []


def test_hot_news_keeps_only_sources_the_search_returned():
    from datetime import date
    from types import SimpleNamespace as Block
    from company_graph.hot_news import read_answer

    hit = Block(url="https://news.example/1", title="강관주 강세", page_age="2026-10-01")
    search = Block(type="web_search_tool_result", content=[hit])
    say = lambda body: Block(type="text", text=body)
    good = read_answer([search, say('찾았습니다.\n{"found": "group", "reason": "LNG 투자 기대에 강관주가 올랐다고 전했다", "urls": ["https://news.example/1"]}')])
    assert (good["found"], good["sources"][0]["title"], good["searches"]) == ("group", "강관주 강세", 1)
    # 검색 결과에 없던 주소를 댔다: 근거가 없으므로 이유도 버린다
    made_up = read_answer([search, say('{"found": "group", "reason": "수주 기대", "urls": ["https://made.up/x"]}')])
    assert (made_up["found"], made_up["reason"], made_up["sources"]) == ("none", "", [])
    assert read_answer([say("기사를 찾지 못했습니다")])["found"] == "none"
    # 그 거래일 무렵의 기사가 아니면 근거로 치지 않는다 (10월 10일에 본 "9 days ago" 는 10월 1일)
    answer = say('{"found": "group", "reason": "강관주 강세", "urls": ["https://news.example/1"]}')
    aged = Block(type="web_search_tool_result", content=[Block(url="https://news.example/1", title="강관주 강세", page_age="9 days ago")])
    assert read_answer([aged, answer], date(2026, 9, 30), date(2026, 10, 10))["found"] == "group"
    assert read_answer([aged, answer], date(2026, 6, 18), date(2026, 10, 10))["found"] == "none"
