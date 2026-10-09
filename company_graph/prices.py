"""상장 주식의 하루 시세(종가, 등락률, 거래대금, 시가총액)를 받아 price_daily 에 넣는다.

출처는 한국거래소 KRX OPEN API(openapi.krx.co.kr)의 일별매매정보다. 장중 값이 아니고, 거래일 다음 날에 나온다.
이용약관: 비상업 목적으로만 쓰고, 받은 데이터를 그대로 제3자에게 넘기지 않으며, 결과로 만든 화면에는
"KRX 통계정보"를 썼다고 밝힌다(제6조, 제10조, 제11조). 그래서 화면과 Agent 는 이 표를 가공한 값만 내보낸다.

실행: python -m company_graph.prices                 (가장 최근에 넣은 날 다음부터 어제까지)
      python -m company_graph.prices 20260101 20261008 (기간을 정해서)
하루에 시장 셋(유가증권, 코스닥, 코넥스)을 한 번씩 부른다. 한도는 열쇠 하나에 하루 10,000회다.
"""
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from sqlalchemy import func, select

from .config import secret
from .db import PriceDaily, session

BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
MARKETS = ("stk_bydd_trd", "ksq_bydd_trd", "knx_bydd_trd")   # 유가증권, 코스닥, 코넥스
FIRST_DAY = date(2026, 1, 2)   # 처음 받을 때 어디서부터 받을지


def _number(text) -> Decimal | None:
    try:
        return Decimal(str(text).replace(",", "")) if text not in (None, "", "-") else None
    except InvalidOperation:
        return None


def parse(rows: list[dict]) -> list[dict]:
    """API 의 줄을 price_daily 의 칸으로 옮긴다. 종가가 없는 줄(거래 정지 등)은 버린다."""
    out = []
    for row in rows:
        code, close = str(row.get("ISU_CD") or "").strip(), _number(row.get("TDD_CLSPRC"))
        if len(code) != 6 or close is None:
            continue
        out.append({"stock_code": code, "trade_date": datetime.strptime(row["BAS_DD"], "%Y%m%d").date(), "close_price": close,
                    "change_pct": _number(row.get("FLUC_RT")), "volume": int(_number(row.get("ACC_TRDVOL")) or 0),
                    "trade_value": int(_number(row.get("ACC_TRDVAL")) or 0), "market_cap": int(_number(row.get("MKTCAP")) or 0)})
    return out


def fetch(day: date) -> list[dict]:
    rows = []
    for market in MARKETS:
        response = requests.get(f"{BASE}/{market}", params={"basDd": day.strftime("%Y%m%d")},
                                headers={"AUTH_KEY": secret("KRX_API_KEY")}, timeout=60)
        response.raise_for_status()
        rows += parse(response.json().get("OutBlock_1") or [])
        time.sleep(0.2)
    return rows


def load(start: date, end: date) -> int:
    total, day = 0, start
    with session() as db:
        while day <= end:
            if day.weekday() < 5:   # 공휴일은 빈 결과가 온다
                rows = fetch(day)
                if rows:
                    db.query(PriceDaily).filter(PriceDaily.trade_date == day).delete()
                    db.bulk_insert_mappings(PriceDaily, rows)
                    db.commit()
                    total += len(rows)
                print(day.isoformat(), len(rows), flush=True)
            day += timedelta(days=1)
    return total


def main():
    if len(sys.argv) >= 3:
        start, end = (datetime.strptime(a, "%Y%m%d").date() for a in sys.argv[1:3])
    else:
        with session() as db:
            last = db.scalar(select(func.max(PriceDaily.trade_date)))
        start, end = (last + timedelta(days=1) if last else FIRST_DAY), date.today() - timedelta(days=1)
    print(f"{start} ~ {end}: {load(start, end)}줄")


if __name__ == "__main__":
    main()
