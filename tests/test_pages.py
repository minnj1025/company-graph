"""분석 페이지(pages.py): 서명이 맞는 답만 남고, 카드는 열 때 DB에서 채운다. Claude API는 부르지 않는다."""
from datetime import date

import pytest

from company_graph import agent_tools, pages
from company_graph.db import UserPage
from test_query import KIA, MOBIS, db  # noqa: F401

ANSWER = "기아는 현대모비스 지분 17.66%를 가지고 있습니다(접수번호 20250313000001)."


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("PAGE_SECRET", "test-secret-for-pages")


def made(db, when="2025-06-01"):  # noqa: F811
    shown = agent_tools.show_on_graph(db, [KIA], views=[{"kind": "ownership", "company_id": KIA, "as_of": when}])
    result = {"answer": ANSWER, "as_of": when, "cited_not_in_results": [], "wording": {"left": []}}
    return pages.draft("기아 지분 구조 알려줘", result, shown["_views"], [{"id": KIA, "name": "기아(주)"}], [KIA], calls=2)


def test_card_remembers_how_to_rebuild_itself(db):  # noqa: F811
    shown = agent_tools.show_on_graph(db, [KIA], views=[{"kind": "ownership", "company_id": KIA, "as_of": "2025-06-01"}])
    assert shown["_views"][0]["spec"] == {"kind": "ownership", "company_id": KIA, "as_of": "2025-06-01"}


def test_only_checked_answers_get_a_draft(db):  # noqa: F811
    page = made(db)
    assert page["draft"]["blocks"] == [{"type": "view", "kind": "ownership", "company_id": KIA, "as_of": "2025-06-01"},
                                       {"type": "text", "text": ANSWER}]
    base = {"answer": ANSWER, "as_of": "2025-06-01", "cited_not_in_results": []}
    assert pages.draft("q", base, [], [], [], calls=0) is None                                  # 조회 없이 한 답
    assert pages.draft("q", {**base, "cited_not_in_results": ["20990101000001"]}, [], [], [], calls=1) is None
    assert pages.draft("q", {**base, "wording": {"left": ["수혜: …"]}}, [], [], [], calls=1) is None


def test_no_draft_without_a_secret(db, monkeypatch):  # noqa: F811
    for name in ("PAGE_SECRET", "ASK_OWNER_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert made(db) is None


def test_changed_draft_is_refused(db):  # noqa: F811
    page = made(db)
    page["draft"]["blocks"][-1]["text"] = "기아는 매수할 만합니다."
    with pytest.raises(pages.PageError) as error:
        pages.create(db, page["draft"], page["sig"], "visitor")
    assert error.value.status == 403


def test_cards_fill_from_db_on_the_made_day_or_today(db):  # noqa: F811
    page = made(db)
    saved = pages.create(db, page["draft"], page["sig"], "visitor")
    assert pages.SLUG.match(saved["slug"]) and saved["title"] == "기아 지분 구조 알려줘"
    assert "edit_key" not in str(db.get(UserPage, saved["slug"]).spec)

    then = pages.read(db, saved["slug"], today=date(2026, 6, 1))
    card = then["blocks"][0]["view"]
    assert then["basis"] == "made" and card["as_of"] == "2025-06-01" and card["investees"][0]["pct"] == 17.66
    assert then["blocks"][1] == {"type": "text", "text": ANSWER}
    assert [s["rcept_no"] for s in then["sources"]] == ["20250313000001"] and then["sources"][0]["filed"] == "2025-03-13"
    # 만든 뒤 페이지의 기업(기아)이 낸 공시: 2026-03 사업보고서 하나. 현대차·모비스가 낸 것은 세지 않는다
    assert then["newer"]["total"] == 1 and then["newer"]["items"][0]["rcept_no"] == "20260312000001"

    now = pages.read(db, saved["slug"], basis="today", today=date(2026, 6, 1))
    assert now["shown_as_of"] == "2026-06-01" and now["blocks"][0]["view"]["investees"][0]["pct"] == 18.1
    assert now["blocks"][1]["text"] == ANSWER   # 글은 그대로


def test_broken_card_does_not_break_the_page(db):  # noqa: F811
    page = made(db)
    saved = pages.create(db, page["draft"], page["sig"], "visitor")
    row = db.get(UserPage, saved["slug"])
    row.spec = {**row.spec, "blocks": [{"type": "view", "kind": "path", "company_id": KIA}] + row.spec["blocks"][1:]}
    db.commit()
    out = pages.read(db, saved["slug"])
    assert out["blocks"][0]["error"] and out["blocks"][1]["type"] == "text"


def test_delete_needs_the_key(db):  # noqa: F811
    page = made(db)
    saved = pages.create(db, page["draft"], page["sig"], "visitor")
    with pytest.raises(pages.PageError):
        pages.delete(db, saved["slug"], "wrong")
    pages.delete(db, saved["slug"], saved["edit_key"])
    with pytest.raises(pages.PageError) as error:
        pages.read(db, saved["slug"])
    assert error.value.status == 404


def test_daily_limit_per_visitor(db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pages, "VISITOR_LIMIT", 2)
    page = made(db)
    for _ in range(2):
        pages.create(db, page["draft"], page["sig"], "visitor")
    with pytest.raises(pages.PageError) as error:
        pages.create(db, page["draft"], page["sig"], "visitor")
    assert error.value.status == 429
    pages.create(db, page["draft"], page["sig"], "owner", owner=True)   # 운영자는 한도 없음
    assert pages.create(db, page["draft"], page["sig"], "someone-else")["slug"]


def test_bad_slug_is_not_found(db):  # noqa: F811
    with pytest.raises(pages.PageError) as error:
        pages.read(db, "../etc")
    assert error.value.status == 404
    assert MOBIS  # 다른 기업도 fixture 에 있다


# ---------- Agent가 직접 짜는 페이지 (save_page) ----------

from contextlib import contextmanager  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402

from company_graph import agent, skills  # noqa: E402
from test_agent import FakeClient, reply  # noqa: E402

WHEN = date(2026, 6, 1)
SEEN = {"20260312000001", "20260309000001"}
BODY = ['{"pct": 18.1, "rcept_no": "20260309000001"}']


def propose(db, blocks, title="기아 지분 한눈에", warned=None):  # noqa: F811
    return pages.propose(db, {"title": title, "blocks": blocks}, WHEN, SEEN, BODY, "기아 지분 구조를 페이지로 만들어 줘", {} if warned is None else warned)


def test_page_tool_and_rules_appear_only_when_turned_on(monkeypatch):
    monkeypatch.delenv("ASK_PAGES", raising=False)
    monkeypatch.delenv("ASK_SKILLS", raising=False)
    before = agent.system()
    assert "make-page" not in before and "make-page" not in skills.names()
    monkeypatch.setenv("ASK_PAGES", "1")
    assert "make-page" in agent.system() and "make-page" in skills.tool()["input_schema"]["properties"]["names"]["items"]["enum"]
    monkeypatch.setenv("ASK_PAGES", "0")
    assert agent.system() == before


def test_good_page_is_accepted_with_cards_built_on_the_day(db):  # noqa: F811
    out = propose(db, [{"type": "text", "text": "기아의 출자 회사를 모았습니다(접수번호 20260309000001)."},
                       {"type": "view", "kind": "ownership", "company_id": KIA}])
    assert out["saved"] and out["cards"][0]["shown"] == "기아(주) 지분 구조도"
    assert out["_page"]["blocks"][1] == {"type": "view", "kind": "ownership", "company_id": KIA, "as_of": "2026-06-01"}
    assert out["_page"]["title"] == "기아 지분 한눈에"


def test_page_problems_go_back_to_the_agent(db):  # noqa: F811
    assert "카드(view)" in propose(db, [{"type": "text", "text": "글만 있습니다."}, {"type": "text", "text": "또 글."}])["error"]
    assert "path" in propose(db, [{"type": "view", "kind": "path", "company_id": KIA}, {"type": "text", "text": "경로."}])["error"]
    out = propose(db, [{"type": "view", "kind": "ownership", "company_id": KIA}, {"type": "text", "text": "근거 20990101000001."}])
    assert "20990101000001" in out["error"]
    out = propose(db, [{"type": "view", "kind": "ownership", "company_id": KIA}, {"type": "text", "text": "자동차 관련주로 묶입니다."}])
    assert "관련주" in out["error"]
    out = propose(db, [{"type": "view", "kind": "ownership", "company_id": KIA}, {"type": "text", "text": "가" * 700}])
    assert "600자" in out["error"]


def test_unchecked_number_is_sent_back_once(db):  # noqa: F811
    warned = {}
    blocks = [{"type": "view", "kind": "ownership", "company_id": KIA}, {"type": "text", "text": "모비스 지분은 27.35%입니다."}]
    assert "27.35%" in propose(db, blocks, warned=warned)["error"]
    again = propose(db, blocks, warned=warned)   # 두 번째에는 받고, 걸린 값을 적어 둔다
    assert again["saved"] and again["_page"]["unverified"] == ["27.35%"]


def test_count_claims_catch_counting_sentences_only():
    # 평가 14판에서 틀린 세 문장
    assert pages.count_claims("2026-01-01 이후 공시된 공급계약은 34건이고, 해지 공시는 없습니다.")
    assert pages.count_claims("마지막 건은 국민연금공단 6.13%(접수번호 20241030000275)이며 그 뒤 보고는 없습니다.")
    assert pages.count_claims("유효한 공급계약은 서른 건이 아니라 세 건입니다.")
    for fine in ("모비스 지분은 18.1%입니다(접수번호 20260309000001).", "계약기간은 24개월이고 금액은 3,642억 원입니다.",
                 "이 DB로 알 수 없는 것: 실적 전망은 담지 않습니다.", "2026년 3분기 영업이익은 1조 2,000억 원입니다.",
                 "그 밖의 계약은 이 DB에 없습니다.", "이 DB는 건별로 공시한 계약만 모은다.",
                 "결정 공시이며 실제 처분 여부는 이 DB에 없습니다(그 뒤 결과 공시는 모으지 않음)."):
        assert not pages.count_claims(fine), fine
    # 평가 15판 P20: DB를 밝히는 괄호가 붙었다고 개수 문장을 건너뛰지 않는다
    assert pages.count_claims("유상증자 결정은 합계 2건입니다(결정 공시 기준, 실제 납입 여부는 이 DB에 없음).")
    assert pages.count_claims("보고 접수된 6명의 비등기임원이 모두 늘렸습니다(거래 단가는 DB에 없음).")
    assert pages.count_claims("유일한 공급계약입니다(해지 여부는 이 DB에 없음).")
    assert pages.count_claims("공급계약은 3건이며, 계약 상대의 재무 상태는 이 DB로 알 수 없습니다.")


def test_count_claims_are_sent_back_once_then_removed(db):  # noqa: F811
    warned = {}
    blocks = [{"type": "view", "kind": "ownership", "company_id": KIA},
              {"type": "text", "text": "기아의 출자 회사를 모았습니다(접수번호 20260309000001). 출자 회사는 3곳뿐입니다."}]
    assert "3곳뿐" in propose(db, blocks, warned=warned)["error"]
    again = propose(db, blocks, warned=warned)
    assert again["saved"] and again["removed"] == ["출자 회사는 3곳뿐입니다."]
    assert again["_page"]["blocks"][1]["text"] == "기아의 출자 회사를 모았습니다(접수번호 20260309000001)."


def test_agent_makes_a_page_in_the_loop(db, monkeypatch):  # noqa: F811
    @contextmanager
    def same_db():
        yield db
    monkeypatch.setattr(agent, "session", same_db)
    monkeypatch.setenv("ASK_PAGES", "1")
    client = FakeClient([
        reply("tool_use", NS(type="tool_use", id="p1", name="save_page", input={
            "title": "기아 지분 한눈에", "blocks": [{"type": "view", "kind": "ownership", "company_id": KIA},
                                                {"type": "text", "text": "기아가 가진 지분을 모았습니다."}]})),
        reply("end_turn", NS(type="text", text="기아의 지분 구조 카드와 설명을 담은 페이지를 만들었습니다.")),
    ])
    out = agent.answer("기아 지분 구조를 페이지로 만들어 줘", as_of=WHEN, client=client)
    assert "save_page" in [t["name"] for t in client.requests[0]["tools"]]
    assert out["page_spec"]["title"] == "기아 지분 한눈에" and out["tool_calls"][0]["error"] is None

    monkeypatch.setenv("ASK_PAGES", "0")
    client = FakeClient([reply("end_turn", NS(type="text", text="답입니다."))])
    out = agent.answer("기아 지분 구조", as_of=WHEN, client=client)
    assert "save_page" not in [t["name"] for t in client.requests[0]["tools"]] and "page_spec" not in out


def test_agent_page_is_stored_without_a_signature(db):  # noqa: F811
    out = propose(db, [{"type": "view", "kind": "ownership", "company_id": KIA}, {"type": "text", "text": "기아가 가진 지분입니다."}])
    spec = out["_page"]
    saved = pages.store(db, {"question": spec["question"], "as_of": spec["as_of"], "blocks": spec["blocks"], "companies": [], "highlight": [KIA]},
                        "visitor", title=spec["title"])
    page = pages.read(db, saved["slug"], today=WHEN)
    assert page["title"] == "기아 지분 한눈에" and page["blocks"][0]["view"]["investees"][0]["pct"] == 18.1
