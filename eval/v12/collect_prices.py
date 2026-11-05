"""평가 12판에 쓸 하루 시세(시가·종가·시가총액·거래대금·등락률)를 KRX OPEN API 에서 받아 data/cache/v12/krx 에 날짜별로 둔다.

price_daily 에는 시가가 없다. 뉴스가 나온 뒤 처음 살 수 있는 값은 다음 거래일 시가라서 따로 받는다.
받은 시세는 저장소에 넣지 않는다(KRX 이용약관: 받은 데이터를 그대로 넘기지 않는다). 결과에는 수익률(%)만 쓴다.

실행: python -m eval.v12.collect_prices 20250102 20261016
"""
import json
import sys
import time
from datetime import date, datetime, timedelta

import requests

from company_graph.config import CACHE_DIR, secret

BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
MARKETS = {"stk_bydd_trd": "KOSPI", "ksq_bydd_trd": "KOSDAQ"}
OUT = CACHE_DIR / "v12" / "krx"


def _num(text):
    try:
        return float(str(text).replace(",", "")) if text not in (None, "", "-") else None
    except ValueError:
        return None


def fetch(day: date) -> dict:
    out = {}
    for path, market in MARKETS.items():
        for attempt in range(4):
            try:
                r = requests.get(f"{BASE}/{path}", params={"basDd": day.strftime("%Y%m%d")},
                                 headers={"AUTH_KEY": secret("KRX_API_KEY")}, timeout=60)
                r.raise_for_status()
                break
            except requests.RequestException:
                time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"KRX {path} {day}")
        for row in r.json().get("OutBlock_1") or []:
            code = str(row.get("ISU_CD") or "").strip()
            o, c = _num(row.get("TDD_OPNPRC")), _num(row.get("TDD_CLSPRC"))
            if len(code) == 6 and c and o:
                # 여러 날 수익률은 등락률(기준가 조정 반영)을 이어 붙여 잰다. 종가끼리 나누면 액면분할·감자 날에 틀린다
                out[code] = [o, c, _num(row.get("MKTCAP")) or 0, _num(row.get("ACC_TRDVAL")) or 0, market, _num(row.get("FLUC_RT")) or 0.0]
        time.sleep(0.2)
    return out


def main():
    start, end = (datetime.strptime(a, "%Y%m%d").date() for a in sys.argv[1:3])
    OUT.mkdir(parents=True, exist_ok=True)
    day = start
    while day <= end:
        path = OUT / f"{day:%Y%m%d}.json"
        if day.weekday() < 5 and not path.exists():
            rows = fetch(day)
            if rows:   # 공휴일은 빈 결과
                path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
            print(day, len(rows), flush=True)
        day += timedelta(days=1)


if __name__ == "__main__":
    main()
