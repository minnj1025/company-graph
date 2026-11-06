"""평가 12판의 뉴스: Google 뉴스 RSS 검색(무료)으로 회사마다 반 달씩 기사 제목을 모은다.

- 대상: 2025-07-01(첫 거래일)의 시가총액 상위 200개 보통주(스팩 제외). 그날 값으로 골라 뒤의 일을 보지 않는다.
- 검색어: "회사 이름" after:시작 before:끝. 한 번에 100건까지 오지만 실제로는 반 달에 10~30건이다.
- 남기는 것: 제목, 언론사, 발행일, 주소. 본문은 받지 않는다. data/cache/v12/news 에 두고 저장소에는 넣지 않는다.
- 발행일은 날짜만 맞다(시각은 07:00·08:00 GMT 로 채워져 온다). 그래서 기사 다음 거래일 시가에 사는 것으로 잰다(run.py).
- 막히면(429·503) 오래 쉬고, 계속 막히면 멈춘다. 우회하지 않는다.

실행: python -m eval.v12.collect_news universe          대상 200곳을 정해 data/cache/v12/universe.json 에 둔다
      python -m eval.v12.collect_news fetch 2025-07-01 2026-10-24 [0:100]   (대상의 일부만)
"""
import json
import random
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import requests
from sqlalchemy import func, select

from company_graph.config import CACHE_DIR
from company_graph.db import Company, PriceDaily, session

OUT = CACHE_DIR / "v12"
SIZE = 200
PICK_DAY = date(2025, 7, 1)


def universe():
    with session() as db:
        day = db.scalar(select(func.min(PriceDaily.trade_date)).where(PriceDaily.trade_date >= PICK_DAY))
        caps = dict(db.execute(select(PriceDaily.stock_code, PriceDaily.market_cap).where(PriceDaily.trade_date == day)).all())
        firms = [c for c in db.scalars(select(Company).where(Company.stock_code.in_(list(caps)))) if "스팩" not in c.label]
    firms = [c for c in firms if c.stock_code.endswith("0")]   # 우선주 코드는 0 으로 끝나지 않는다
    firms.sort(key=lambda c: -(caps.get(c.stock_code) or 0))
    rows = [{"company_id": c.company_id, "stock_code": c.stock_code, "name": c.label, "market_cap_rank": i + 1}
            for i, c in enumerate(firms[:SIZE])]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "universe.json").write_text(json.dumps({"picked_on": day.isoformat(), "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(day, len(rows), [r["name"] for r in rows[:10]])


def halves(start: date, end: date):
    day = start
    while day < end:
        mid = day.replace(day=16) if day.day < 16 else (day.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield day, min(mid, end)
        day = mid


def rss(query: str) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (research; company-graph)"})
        except requests.RequestException:
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code in (429, 503):
            print("  막힘", r.status_code, "쉼", 120 * (attempt + 1), flush=True)
            time.sleep(120 * (attempt + 1))
            continue
        if r.status_code == 404:   # 결과가 없는 검색에 404 가 올 때가 있다
            return []
        r.raise_for_status()
        items = []
        for it in ET.fromstring(r.content).findall(".//item"):
            published = parsedate_to_datetime(it.findtext("pubDate"))
            items.append({"title": it.findtext("title"), "source": it.findtext("source"), "date": published.date().isoformat(),
                          "link": it.findtext("link")})
        return items
    raise RuntimeError("계속 막힘: 멈춘다")


def fetch(start: date, end: date, part: str = "0:200"):
    lo, hi = (int(x) for x in part.split(":"))
    rows = json.loads((OUT / "universe.json").read_text(encoding="utf-8"))["rows"][lo:hi]
    done = 0
    for row in rows:
        folder = OUT / "news" / row["stock_code"]
        folder.mkdir(parents=True, exist_ok=True)
        for a, b in halves(start, end):
            path = folder / f"{a:%Y%m%d}.json"
            if path.exists():
                continue
            items = rss(f'"{row["name"]}" after:{a.isoformat()} before:{b.isoformat()}')
            path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            done += 1
            time.sleep(1.0 + random.random())
        print(row["market_cap_rank"], row["name"], "done", done, flush=True)


def main():
    if sys.argv[1] == "universe":
        universe()
    else:
        fetch(*(datetime.strptime(a, "%Y-%m-%d").date() for a in sys.argv[2:4]), *sys.argv[4:5])


if __name__ == "__main__":
    main()
