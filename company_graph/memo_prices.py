"""뉴스 메모의 회사들이 뉴스 뒤에 실제로 어떻게 움직였는지: 뉴스 전 거래일 종가를 0으로 둔 누적 수익률(%).

price_daily(KRX 일별매매정보)를 가공한 값만 내보낸다. 가격 자체는 내보내지 않는다(KRX 이용약관).
시장은 두 가지로 낸다: 모든 상장 종목의 시가총액 가중 평균(평가 12판과 같은 기준, 삼성전자·SK하이닉스 비중이 크다)과
보통 종목의 움직임인 수익률 중앙값.
사후 기록일 뿐이며, 공시 관계가 주가를 움직였다는 뜻이 아니다.
"""
import statistics
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from .db import Company, PriceDaily

AFTER = 10      # 뉴스 날부터 몇 거래일을 보일지
SOURCE = "KRX 통계정보"


def _closes(db, day: date) -> dict[str, tuple[Decimal, int]]:
    rows = db.execute(select(PriceDaily.stock_code, PriceDaily.close_price, PriceDaily.market_cap).where(PriceDaily.trade_date == day))
    return {code: (close, cap or 0) for code, close, cap in rows if close}


def pct(now: Decimal, base: Decimal) -> float:
    return round(float(now / base - 1) * 100, 2)


def market_change(base: dict[str, tuple[Decimal, int]], now: dict[str, tuple[Decimal, int]]) -> float | None:
    """뉴스 전 거래일의 시가총액으로 가중한 평균 수익률(%)."""
    weight = total = 0.0
    for code, (close, cap) in base.items():
        if code in now and cap > 0:
            weight += cap
            total += cap * float(now[code][0] / close - 1)
    return round(total / weight * 100, 2) if weight else None


def median_change(base: dict[str, tuple[Decimal, int]], now: dict[str, tuple[Decimal, int]]) -> float | None:
    """같은 날 시세가 있는 상장 종목 수익률의 중앙값(%)."""
    values = [float(now[code][0] / close - 1) for code, (close, _) in base.items() if code in now]
    return round(statistics.median(values) * 100, 2) if values else None


def memo_prices(db, day: date, company_ids: list[int]) -> dict:
    companies = list(db.scalars(select(Company).where(Company.company_id.in_(company_ids), Company.stock_code.isnot(None))))
    base_day = db.scalar(select(PriceDaily.trade_date).where(PriceDaily.trade_date < day, PriceDaily.trade_date >= day - timedelta(days=14))
                         .order_by(PriceDaily.trade_date.desc()).limit(1))
    out = {"news_day": day.isoformat(), "source": SOURCE, "base_day": None, "days": [], "market": [], "median": [], "companies": []}
    if base_day is None:
        out["note"] = "그 무렵의 시세를 보관하고 있지 않습니다 (최근 약 100일만 둡니다)"
        return out
    days = list(db.scalars(select(PriceDaily.trade_date).where(PriceDaily.trade_date >= day).group_by(PriceDaily.trade_date)
                           .order_by(PriceDaily.trade_date).limit(AFTER)))
    out["base_day"] = base_day.isoformat()
    if not days:
        out["note"] = "뉴스 날 뒤의 시세가 아직 없습니다 (시세는 거래일 다음 날 들어옵니다)"
        return out
    base = _closes(db, base_day)
    later = [_closes(db, d) for d in days]
    out["days"] = [d.isoformat() for d in days]
    out["market"] = [market_change(base, now) for now in later]
    out["median"] = [median_change(base, now) for now in later]
    order = {cid: i for i, cid in enumerate(company_ids)}
    for company in sorted(companies, key=lambda c: order.get(c.company_id, 0)):
        start = base.get(company.stock_code)
        series = [pct(now[company.stock_code][0], start[0]) if start and company.stock_code in now else None for now in later]
        out["companies"].append({"company_id": company.company_id, "name": company.label, "series": series})
    return out
