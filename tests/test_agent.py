"""Agent 루프. Claude API는 부르지 않고 정해진 응답을 돌려주는 가짜 클라이언트로 돈다."""
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace as NS

from company_graph import agent
from test_query import KIA, db  # noqa: F401


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.messages = NS(create=self.create)

    def create(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})
        return self.replies.pop(0)


def reply(stop, *blocks):
    return NS(stop_reason=stop, content=list(blocks), usage=NS(input_tokens=100, output_tokens=10,
                                                                cache_read_input_tokens=0, cache_creation_input_tokens=0))


def test_tool_call_then_answer_and_citation_check(db, monkeypatch):
    @contextmanager
    def same_db():
        yield db
    monkeypatch.setattr(agent, "session", same_db)
    client = FakeClient([
        reply("tool_use", NS(type="tool_use", id="t1", name="get_relations",
                             input={"company_id": KIA, "as_of": "2026-06-30", "rel_type": "equity", "direction": "out"})),
        reply("end_turn", NS(type="text", text="기아는 현대모비스 지분 18.1%를 갖고 있습니다 (20260312000001). 참고 20991231000009")),
    ])
    out = agent.answer("기아가 가진 현대모비스 지분은?", as_of=date(2026, 6, 30), client=client)
    assert out["tool_calls"][0]["total"] == 1 and out["usage"]["input_tokens"] == 200
    assert out["cited_not_in_results"] == ["20991231000009"]     # 도구 결과에 없던 접수번호는 잡아낸다
    sent = client.requests[1]["messages"]
    assert sent[0]["content"].startswith("조회 시점: 2026-06-30") and sent[2]["content"][0]["tool_use_id"] == "t1"
    assert sent[2]["content"][0]["is_error"] is False
