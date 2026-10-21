"""내 종목: 비중은 주되 종가는 화면에 내지 않는다 (거래소 시세 약관)."""
import json
from datetime import date
from types import SimpleNamespace

from company_graph import portfolio


def test_summary_gives_weights_and_changes_but_not_prices(monkeypatch):
    companies = [SimpleNamespace(company_id=1, label="가", stock_code="000001", corp_cls="Y"),
                 SimpleNamespace(company_id=2, label="나", stock_code="000002", corp_cls="K"),
                 SimpleNamespace(company_id=3, label="다", stock_code="000003", corp_cls="K")]

    class DB:
        def scalars(self, _):
            return companies

    moves = {1: {"day": "2026-10-16", "close": 1000.0, "stale": False, "d1": 1.5, "spark": [0, 1.5]},
             2: {"day": "2026-10-16", "close": 3000.0, "stale": False, "d1": -2.0, "spark": [0, -2.0]},
             3: {"day": "2026-10-16", "close": 500.0, "stale": False, "d1": 0.0, "spark": [0, 0]}}
    monkeypatch.setattr(portfolio, "moves", lambda db, found: (moves, "2026-10-16"))
    monkeypatch.setattr(portfolio, "events", lambda *args: [])
    monkeypatch.setattr(portfolio, "issues", lambda *args: [])
    result = portfolio.summary(DB(), [(1, 30), (2, 10), (3, None)], 30, date(2026, 10, 21))
    weights = {s["id"]: s["weight"] for s in result["stocks"]}
    assert weights == {1: 50.0, 2: 50.0, 3: None}   # 30주 × 1,000 = 10주 × 3,000. 관심 종목(수량 없음)은 비중이 없다
    assert result["stocks"][1]["move"]["d1"] == -2.0
    assert '"close"' not in json.dumps(result, ensure_ascii=False)
