from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from company_graph.db import Base, Company, PriceDaily
from company_graph.memo_prices import memo_prices


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    # 회사1은 작고 +10% → +20%, 회사2는 크고 -10% → -20%, 회사3은 그대로
    rows = {"000001": (100, 110, 120, 10), "000002": (100, 90, 80, 90), "000003": (100, 100, 100, 1)}
    for i, code in enumerate(rows, 1):
        db.add(Company(company_id=i, name=f"회사{i}", stock_code=code, corp_cls="Y", in_scope=True))
    for code, (before, first, second, cap) in rows.items():
        for day, close in ((date(2026, 10, 2), before), (date(2026, 10, 6), first), (date(2026, 10, 7), second)):
            db.add(PriceDaily(stock_code=code, trade_date=day, close_price=Decimal(close), volume=1, market_cap=cap))
    db.commit()
    return db


def test_changes_from_the_close_before_the_news():
    """뉴스 전 거래일 종가를 0으로 둔 누적 %. 시장은 시가총액 가중과 중앙값 둘. 가격은 내보내지 않는다."""
    out = memo_prices(_db(), date(2026, 10, 6), [2, 1])
    assert out["base_day"] == "2026-10-02" and out["days"] == ["2026-10-06", "2026-10-07"]
    assert [c["name"] for c in out["companies"]] == ["회사2", "회사1"]   # 부른 순서대로
    assert out["companies"][1]["series"] == [10.0, 20.0]
    assert out["median"] == [0.0, 0.0]
    assert out["market"] == [-7.92, -15.84]   # (10×10% + 90×-10% + 1×0%) / 101: 가중치 큰 회사2 쪽으로 쏠린다
    assert "close" not in str(out) and "110" not in str(out)


def test_no_prices_kept_for_that_day():
    out = memo_prices(_db(), date(2026, 7, 2), [1])
    assert out["days"] == [] and "보관" in out["note"]


def test_last_changes_for_the_list():
    from company_graph.memo_prices import last_changes
    out = last_changes(_db(), [(date(2026, 10, 6), [1, 2]), (date(2026, 7, 2), [1])])
    assert out[0] == {"days": 2, "median": 0.0, "companies": {1: 20.0, 2: -20.0}}
    assert out[1]["days"] == 0 and out[1]["companies"] == {}
