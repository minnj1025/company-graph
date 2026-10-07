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
