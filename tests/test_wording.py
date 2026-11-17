"""답에 쓰지 않기로 한 표현 검사(wording.py). Claude API는 부르지 않는다."""
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace as NS

from company_graph import agent, wording
from test_agent import FakeClient, reply
from test_query import db  # noqa: F401


def names(answer, question=""):
    return [f.split(":")[0] for f in wording.flagged(answer, question)]


def test_finds_banned_words():
    # 평가 13판에서 채점자가 센 두 답
    assert names("9월 7일 변압기 관련주의 상승 원인은 이 DB에서 확인되지 않습니다.") == ["관련주"]
    assert names("발표 뒤 위성 관련주도 함께 내렸습니다.") == ["관련주"]
    assert names("정책 수혜가 기대되는 회사입니다.") == ["수혜"]
    assert names("실시간 시세로 보면 2% 올랐습니다.") == ["실시간"]
    assert names("지금은 분할 매수를 추천합니다.") == ["매매 권유"]
    assert names("이 회사 주가는 다음 달 오를 것으로 보입니다.") == ["주가 전망"]


def test_leaves_quotes_refusals_and_the_question_alone():
    assert names("기사 제목은 「[특징주] 진단키트 관련주 강세」입니다.") == []
    assert names('뉴스핌 "반도체 관련주 급등" 기사를 찾았습니다.') == []
    assert names("자동 요약에 따르면 우주항공 관련주가 일제히 내렸다고 합니다.") == []
    assert names("수혜 여부는 이 DB로 확인되지 않습니다.") == []
    assert names("매수·매도 판단은 드리지 않습니다.") == []
    assert names("관련주라는 말은 쓰지 않습니다.") == []
    assert names("위성 관련주 5곳이 함께 내렸습니다.", "10월 7일에 위성 관련주가 왜 빠졌나요?") == []
    assert names("관련 주식 수는 1,200주입니다. 관련 주가 자료는 없습니다.") == []


def test_agent_rewrites_once(db, monkeypatch):  # noqa: F811
    @contextmanager
    def same_db():
        yield db
    monkeypatch.setattr(agent, "session", same_db)
    monkeypatch.setenv("ASK_WORDING", "1")
    client = FakeClient([
        reply("end_turn", NS(type="text", text="변압기 관련주가 함께 올랐습니다.")),
        reply("end_turn", NS(type="text", text="변압기를 만드는 회사가 함께 올랐습니다.")),
    ])
    out = agent.answer("9월 7일에 변압기 만드는 회사들이 왜 올랐나요?", as_of=date(2026, 9, 7), client=client)
    assert out["answer"] == "변압기를 만드는 회사가 함께 올랐습니다."
    sent = client.requests[1]["messages"][-1]["content"]
    assert sent.startswith("표현 검사") and "관련주" in sent
    assert out["wording"]["retried"] and out["wording"]["left"] == []


def test_agent_gives_up_after_one_rewrite(db, monkeypatch):  # noqa: F811
    @contextmanager
    def same_db():
        yield db
    monkeypatch.setattr(agent, "session", same_db)
    monkeypatch.setenv("ASK_WORDING", "1")
    client = FakeClient([reply("end_turn", NS(type="text", text="정책 수혜가 큽니다."))] * 2)
    out = agent.answer("이 회사 어때?", as_of=date(2026, 9, 7), client=client)
    assert len(client.requests) == 2 and out["wording"]["left"]


def test_off_by_default(db, monkeypatch):  # noqa: F811
    @contextmanager
    def same_db():
        yield db
    monkeypatch.setattr(agent, "session", same_db)
    monkeypatch.delenv("ASK_WORDING", raising=False)
    client = FakeClient([reply("end_turn", NS(type="text", text="정책 수혜가 큽니다."))])
    out = agent.answer("이 회사 어때?", as_of=date(2026, 9, 7), client=client)
    assert len(client.requests) == 1 and "wording" not in out
