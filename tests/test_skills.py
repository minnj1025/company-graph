"""질문 유형별 규칙(skill). Claude API는 부르지 않는다."""
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace as NS

import pytest

from company_graph import agent, skills
from test_agent import FakeClient, reply
from test_query import db  # noqa: F401


def lines(text):
    return [line for line in text.split("\n") if line.strip()]


def test_skill_bodies_are_copied_word_for_word():
    # 규칙 본문은 원래 지시문의 절과 글자까지 같다. 두 방식의 차이는 규칙의 위치뿐이다
    found = skills.load()
    assert len(found) == 7
    for name, item in found.items():
        source = agent.WHY if name == "why-questions" else agent.SYSTEM
        assert item["body"] in source, name
        assert item["description"]


@pytest.mark.parametrize("style", ["1", "2"])
def test_core_prompt_keeps_every_other_line(monkeypatch, style):
    monkeypatch.setenv("ANSWER_STYLE", style)
    monkeypatch.setenv("ASK_SKILLS", "0")
    whole = agent.system()
    monkeypatch.setenv("ASK_SKILLS", "1")
    core = agent.system()
    bodies = [agent._skill_text(n, item["body"]) for n, item in skills.load().items()]
    for body in bodies:
        assert body not in core
    # 원래 지시문의 줄 = 줄인 지시문의 줄(목록 빼고) + skill 본문의 줄
    catalog = lines(skills.catalog())
    assert sorted(lines(whole)) == sorted([x for x in lines(core) if x not in catalog] + [x for b in bodies for x in lines(b)])
    assert all(x in core for x in catalog)
    assert len(core) < len(whole) * 0.7


def test_without_why_rules(monkeypatch):
    monkeypatch.setenv("ASK_SKILLS", "1")
    monkeypatch.setenv("ASK_WHY", "0")
    assert "why-questions" not in agent.system()
    assert "why-questions" not in skills.tool(why=False)["input_schema"]["properties"]["names"]["items"]["enum"]
    assert "error" in skills.read({"names": ["why-questions"]}, why=False)


def test_read_skill():
    out = skills.read({"names": ["financials", "financials", "price-moves"]})
    assert [s["name"] for s in out["skills"]] == ["financials", "price-moves"]
    assert out["skills"][0]["rules"].startswith("실적과 재무를 묻는 질문")
    assert "error" in skills.read({"names": ["nope"]}) and "error" in skills.read({})


def test_styled_why_rules_follow_answer_style(monkeypatch):
    monkeypatch.setenv("ANSWER_STYLE", "2")
    text = agent._skill_text("why-questions", skills.load()["why-questions"]["body"])
    assert "같은 문단에 가장 눈에 띄는 사실 하나를 바로 적습니다" in text


def test_agent_reads_skill_in_the_loop(db, monkeypatch):  # noqa: F811
    @contextmanager
    def same_db():
        yield db
    monkeypatch.setattr(agent, "session", same_db)
    monkeypatch.setenv("ASK_SKILLS", "1")
    client = FakeClient([
        reply("tool_use", NS(type="tool_use", id="s1", name="read_skill", input={"names": ["financials"]})),
        reply("end_turn", NS(type="text", text="2분기 실적은 아직 공시되지 않았습니다.")),
    ])
    seen = []
    out = agent.answer("기아 2분기 영업이익은?", as_of=date(2026, 6, 30), client=client, on_result=lambda n, a, r: seen.append(n))
    first = client.requests[0]
    assert any(t["name"] == "read_skill" for t in first["tools"])
    said = first["system"][0]["text"]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}   # 질문끼리 앞부분 캐시를 함께 쓴다
    assert "질문 유형별 규칙 (skill)" in said and "get_financials 를 씁니다" not in said
    sent = client.requests[1]["messages"][2]["content"][0]
    assert sent["is_error"] is False and "get_financials 를 씁니다" in sent["content"]
    assert out["tool_calls"][0]["tool"] == "read_skill" and seen == ["read_skill"]


def test_default_is_unchanged(monkeypatch):
    monkeypatch.delenv("ASK_SKILLS", raising=False)
    monkeypatch.delenv("ANSWER_STYLE", raising=False)
    monkeypatch.delenv("ASK_WHY", raising=False)
    assert agent.system() == agent.SYSTEM.replace("\n화면\n", "\n" + agent.WHY + "\n화면\n", 1)


@pytest.mark.parametrize("style", ["1", "2"])
def test_keep_why_rules_in_prompt(monkeypatch, style):
    # ASK_SKILLS=2: 원인·평가 규칙은 지금 지시문과 같은 자리에 두고, 나머지 6묶음만 펼친다
    monkeypatch.setenv("ANSWER_STYLE", style)
    monkeypatch.setenv("ASK_SKILLS", "0")
    whole = agent.system()
    monkeypatch.setenv("ASK_SKILLS", "2")
    core = agent.system()
    why = agent._skill_text("why-questions", skills.load()["why-questions"]["body"])
    assert why in core and "- why-questions:" not in core
    bodies = [item["body"] for n, item in skills.load().items() if n != "why-questions"]
    assert all(b not in core for b in bodies)
    catalog = lines(skills.catalog(why=False))
    assert sorted(lines(whole)) == sorted([x for x in lines(core) if x not in catalog] + [x for b in bodies for x in lines(b)])
    assert "why-questions" not in skills.tool(why=agent._skill_why())["input_schema"]["properties"]["names"]["items"]["enum"]
