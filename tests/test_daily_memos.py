"""매일 메모: 전날 뉴스에서 메모할 것을 고르는 규칙."""
from company_graph.daily_memos import choose


def row(company_id, title, imp, about=True, cat="산업"):
    return {"company_id": company_id, "title": title, "imp": imp, "about": about, "cat": cat}


def test_choose_core_news_by_importance_one_per_company_and_title():
    labeled = [row(1, "A사 2조 수주", 0.9), row(1, "A사 신제품", 0.8), row(2, "A사 2조 수주", 0.9),   # 같은 사건이 두 회사에서 잡힘
               row(3, "C사 주가 급등", 0.95, cat="경제"), row(4, "D사 언급", 0.95, about=False), row(5, "E사 증설", 0.6), row(6, "F사 인수", 0.75)]
    core, chosen = choose(labeled, limit=10)
    assert len(core) == 4
    assert [(r["company_id"], r["title"]) for r in chosen] == [(1, "A사 2조 수주"), (6, "F사 인수")]
    assert len(choose(labeled, limit=1)[1]) == 1


def test_same_event_from_several_companies_is_one_memo():
    labeled = [{**row(1, "HD현대중공업, 6301억 암모니아 운반선 4척 수주", 0.9), "kind": "수주·공급계약"},
               {**row(2, "HD한국조선해양, 암모니아선 6,301억 수주…2030년 일감", 0.85), "kind": "수주·공급계약"},
               {**row(3, "B사, 2030년까지 공장 증설", 0.8), "kind": "투자·증설"}]
    assert [r["company_id"] for r in choose(labeled)[1]] == [1, 3]   # 연도(2030)는 같은 사건의 표시로 보지 않는다
