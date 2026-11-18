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
