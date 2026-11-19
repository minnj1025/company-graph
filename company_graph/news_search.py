"""뉴스 분석 탭의 검색: 사용자가 적은 회사나 주제로 최근 기사 제목을 Google 뉴스 RSS에서 찾는다.

Claude를 부르지 않아 무료다. 받는 것은 제목, 언론사, 날짜, 원문 링크뿐이고 기사 본문은 받지도 저장하지도 않는다.
매일 아침 작업(daily_memos.py)이 회사별 제목을 모을 때와 같은 곳, 같은 거르기 규칙을 쓴다.
서비스 서버에는 requests 가 없어 표준 라이브러리(urllib)로 부른다.
"""

import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))
DAYS, LIMIT = 7, 15
KEEP_SECONDS = 600   # 같은 검색어는 10분 동안 다시 묻지 않는다
# 주가·시황 기사는 공시로 이어 볼 사건이 아니다 (평가 12판 지시문의 '단순 주가·시황 변동, 증권사 목표가'). 매일 아침 작업도 이 규칙으로 뺀다
MARKET_TALK = re.compile(r"특징주|주가|목표가|목표주가|급등|급락|신고가|상한가|하한가|시가총액|시총|순매수|순매도|외국인|코스피|코스닥|증시|\[포토\]|\[인사\]|부고")

_kept: dict[str, tuple[float, list[dict]]] = {}


def clean_title(title: str) -> str:
    return re.sub(r"\s+-\s+[^-]{1,40}$", "", title or "").strip()   # 끝의 " - 언론사"


def kst_today() -> date:
    return datetime.now(KST).date()


def parse(xml: bytes, limit: int = LIMIT) -> list[dict]:
    """RSS 를 제목 목록으로: 주가·시황 기사와 같은 제목은 빼고, 최신순으로. 날짜는 한국 날짜다."""
    items, seen = [], set()
    for it in ET.fromstring(xml).findall(".//item"):
        source = (it.findtext("source") or "").strip() or None
        title = clean_title(it.findtext("title"))
        if source and title.endswith(f" - {source}"):   # 언론사가 제목 끝에 이름을 한 번 더 붙인 것 ('… 3642억원 규모 - 머니투데이 - 머니투데이')
            title = title[: -len(source) - 3].strip()
        link = (it.findtext("link") or "").strip()
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST)
        except (TypeError, ValueError):
            continue
        if not title or title in seen or MARKET_TALK.search(title) or not link.startswith("https://"):   # 화면이 그대로 여는 링크다
            continue
        seen.add(title)
        items.append({"title": title, "source": source,
                      "day": min(published.date(), kst_today()).isoformat(), "at": published.isoformat(), "link": link})
    items.sort(key=lambda x: x["at"], reverse=True)
    return items[:limit]


def search(q: str, days: int = DAYS) -> list[dict] | None:
    """최근 days 일 기사. Google 이 막거나 답하지 않으면 None (화면은 제목을 직접 넣게 한다)."""
    q = " ".join(q.split())
    key = f"{q}|{days}"
    kept = _kept.get(key)
    if kept and time.time() - kept[0] < KEEP_SECONDS:
        return kept[1]
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": f"{q} when:{days}d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research; company-graph)"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            items = parse(response.read())
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return None
        items = []
    except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError):
        return None
    if len(_kept) > 500:
        _kept.clear()
    _kept[key] = (time.time(), items)
    return items
