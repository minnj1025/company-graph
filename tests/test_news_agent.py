"""뉴스 메모 Agent 루프. Claude API는 부르지 않고 정해진 응답을 돌려주는 가짜 클라이언트로 돈다."""
from contextlib import contextmanager
from types import SimpleNamespace as NS

from company_graph import news_agent
from test_agent import FakeClient
from test_query import KIA, MOBIS, db  # noqa: F401

NEWS = {"title": "기아, 새 전기차 부품 계약", "date": "2026-07-01", "company": "기아"}


def reply(stop, *blocks):
    return NS(model="claude-haiku-5-5", stop_reason=stop, content=list(blocks),
              usage=NS(input_tokens=100, output_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def memo(rcept_no):
    return {"summary": "기아가 부품 계약을 맺었다.", "subjects": [{"company_id": KIA, "role": "계약을 맺음"}], "topics": ["전기차 부품"],
            "subject_facts": [], "unknown": [], "next": [],
            "related": [{"company_id": MOBIS, "link": "지분·계열", "closeness": "직접", "fact": "기아가 현대모비스 지분을 가짐",
                         "why": "같은 계열의 부품사", "rcept_nos": [rcept_no]}]}


def replies():
    return [
        reply("tool_use", NS(type="text", text="기아의 지분 관계부터 봅니다."),
              NS(type="tool_use", id="t1", name="get_relations",
                 input={"company_id": KIA, "as_of": "2026-06-30", "rel_type": "equity", "direction": "out"})),
        reply("tool_use", NS(type="tool_use", id="t2", name="write_memo", input=memo("20991231000009"))),   # 조회 결과에 없는 번호
        reply("tool_use", NS(type="tool_use", id="t3", name="write_memo", input=memo("20260312000001"))),
    ]


def test_progress_events_do_not_change_what_the_model_sees(db, monkeypatch):
    @contextmanager
    def same_db():
        yield db
    monkeypatch.setattr(news_agent, "session", same_db)
    quiet, loud = FakeClient(replies()), FakeClient(replies())
    plain = news_agent.run(NEWS, client=quiet)
    events, results = [], []
    shown = news_agent.run(NEWS, client=loud, on_event=lambda kind, value: events.append((kind, value)),
                           on_result=lambda name, arguments, result: results.append(name))
    assert [r["messages"] for r in quiet.requests] == [r["messages"] for r in loud.requests]
    assert plain["memo"] == shown["memo"] and shown["memo"]["related"][0]["rcept_nos"] == ["20260312000001"]
    assert [kind for kind, _ in events] == ["say", "tool", "result", "check", "memo"]
    assert events[2][1]["total"] == 1 and MOBIS in events[2][1]["companies"]
    assert "20991231000009" in events[3][1]["problems"][0]
    assert results == ["get_relations"]


def test_number_check_only_when_turned_on(db, monkeypatch):
    @contextmanager
    def same_db():
        yield db
    monkeypatch.setattr(news_agent, "session", same_db)

    def with_fact(fact):
        m = memo("20260312000001")
        m["related"][0]["fact"] = fact
        return m

    def script():
        return [replies()[0],
                reply("tool_use", NS(type="tool_use", id="t2", name="write_memo", input=with_fact("기아가 현대모비스 지분 99.9%를 가짐"))),
                reply("tool_use", NS(type="tool_use", id="t3", name="write_memo", input=with_fact("기아가 현대모비스 지분을 가짐")))]
    off = news_agent.run(NEWS, client=FakeClient(script()))
    assert off["first_problems"] == [] and "99.9" in off["memo"]["related"][0]["fact"]   # 끄면 예전과 같다(첫 메모를 받는다)
    events = []
    on = news_agent.run(NEWS, client=FakeClient(script()), check_numbers=True, on_event=lambda kind, value: events.append((kind, value)))
    assert "99.9" in on["first_problems"][0] and on["memo"]["related"][0]["unchecked_numbers"] == []
    assert [kind for kind, _ in events][-2:] == ["check", "memo"]


def test_unseen_numbers_ignores_commas_and_news_numbers():
    seen = '{"value_text": "1조 742억 원", "ratio": 36.60, "date": "2026-09-22", "value": 1074200000000}'
    assert news_agent.unseen_numbers("2026-09-22 1조 742억 원(매출액 대비 36.6%), 1,074,200,000,000원", seen, "") == []
    assert news_agent.unseen_numbers("2만4480만원 공급", seen, "") == ["4480"]
    assert news_agent.unseen_numbers("3605억원 수주", seen, "삼성전기, 메모리 기판 3605억원 수주") == []
