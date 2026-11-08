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


def test_nearly_equal_amounts_are_the_same_event():
    labeled = [{**row(1, "HD한국조선해양, 암모니아선 6301억 수주", 0.9), "kind": "수주·공급계약"},
               {**row(2, "HD현대중공업, VLAC 4척 공급키로… 6300억원 규모", 0.85), "kind": "수주·공급계약"},
               {**row(3, "C사 6300억 공장 증설", 0.8), "kind": "투자·증설"}]   # 종류가 다르면 다른 사건
    assert [r["company_id"] for r in choose(labeled)[1]] == [1, 3]


def test_event_already_memoed_on_an_earlier_day_is_skipped():
    earlier = [{"title": "HD한국조선해양, 암모니아선 6301억 수주", "kind": "수주·공급계약"}, {"title": "D사 신제품 공개", "kind": "신제품·기술"}]
    labeled = [{**row(1, "HD한국조선해양, VLAC 4척 6301억원 수주…연간 목표 91% 달성", 0.9), "kind": "수주·공급계약"},   # 다음 날 이어진 기사
               {**row(2, "HD현대중공업, 6300억원 규모 VLAC 공급", 0.85), "kind": "수주·공급계약"},
               {**row(3, "D사 신제품 공개", 0.8), "kind": "신제품·기술"},   # 같은 제목
               {**row(4, "E사 6301억 공장 증설", 0.8), "kind": "투자·증설"},   # 종류가 다르면 다른 사건
               {**row(1, "HD한국조선해양, 1조 LNG선 수주", 0.75), "kind": "수주·공급계약"}]   # 같은 회사의 새 사건
    assert [r["title"] for r in choose(labeled, earlier=earlier)[1]] == ["E사 6301억 공장 증설", "HD한국조선해양, 1조 LNG선 수주"]
