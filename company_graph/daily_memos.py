"""매일 아침: 전날 상장사 뉴스에서 핵심을 스스로 골라, 뉴스 메모 Agent 로 메모를 남긴다 (유료, claude-haiku-5-5).

1. 대상: 최근 거래일 시가총액 상위 200개 보통주(스팩·우선주 제외)
2. 뉴스: Google 뉴스 RSS 검색으로 회사마다 전날 기사 제목을 모은다(무료). 막히면 그 회사는 건너뛴다. 본문은 받지 않는다
3. 고르기: 평가 12판과 같은 제목 분류 지시문(eval/v12/classify.py)으로 주인공·분야·중요도를 매기고,
   주인공이 그 회사이고 산업 뉴스이며 중요도 0.7 이상인 것을 중요도 순으로. 한 회사에서 한 건, 같은 제목은 한 번
4. 메모: news_agent.run(check_numbers=True). 조회 시점은 뉴스 전날(화면의 뉴스 메모와 같다)
5. 저장: news_memo 표. 그날 이미 메모가 있으면 다시 하지 않는다(매일 작업이 여러 번 돌아도 한 번만 쓴다)
하루 상한: 분류와 메모를 합쳐 cap 달러를 넘으면 멈춘다(기본 $0.2, 10건)

실행: python -m company_graph.daily_memos store [--day=2026-11-06] [--limit=10] [--cap=0.2]
      python -m company_graph.daily_memos pick [--day=...]      고르기까지만 돌려 찍는다 (분류는 유료, 메모와 저장은 안 함)
"""
import json
import random
import re
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import func, select

from . import news_agent
from .config import secret
from .db import Base, Company, NewsMemo, PriceDaily, get_engine, session

MODEL = news_agent.MODEL
SIZE, LIMIT, CAP, MIN_IMP = 200, 10, 0.2, 0.7
PER_FIRM = 10   # 한 회사에서 분류할 제목 수의 상한 (큰 회사는 하루 100건 가까이 나온다. 30건이면 분류에만 하루 $0.12 가 들었다)
# 분류기가 어차피 낮게 매기는 주가·시황 기사(평가 12판 지시문의 '단순 주가·시황 변동, 증권사 목표가')는 묻기 전에 뺀다
MARKET_TALK = re.compile(r"특징주|주가|목표가|목표주가|급등|급락|신고가|상한가|하한가|시가총액|시총|순매수|순매도|외국인|코스피|코스닥|증시|\[포토\]|\[인사\]|부고")
EVENT_NUMBER = re.compile(r"\d[\d,.]{2,}")
KST = timezone(timedelta(hours=9))


def universe(db, day: date) -> list[Company]:
    """day 이전의 가장 최근 거래일 시가총액 상위 SIZE 개 보통주."""
    trade = db.scalar(select(func.max(PriceDaily.trade_date)).where(PriceDaily.trade_date <= day))
    caps = dict(db.execute(select(PriceDaily.stock_code, PriceDaily.market_cap).where(PriceDaily.trade_date == trade)).all())
    firms = [c for c in db.scalars(select(Company).where(Company.stock_code.in_(list(caps))))
             if "스팩" not in c.label and c.stock_code.endswith("0")]   # 우선주 코드는 0 으로 끝나지 않는다
    return sorted(firms, key=lambda c: -(caps.get(c.stock_code) or 0))[:SIZE]


def _clean(title: str) -> str:
    return re.sub(r"\s+-\s+[^-]{1,40}$", "", title or "").strip()   # 끝의 " - 언론사"


def headlines(name: str, day: date) -> list[dict] | None:
    """그날 나온 기사 제목들. 막히면 None (그 회사는 건너뛴다)."""
    import requests   # 수집할 때만 필요하다 (서비스 서버에는 없다)

    query = f'"{name}" after:{(day - timedelta(days=1)).isoformat()} before:{(day + timedelta(days=1)).isoformat()}'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (research; company-graph)"})
    except requests.RequestException:
        return None
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        return None
    items, seen = [], set()
    for it in ET.fromstring(r.content).findall(".//item"):
        published = parsedate_to_datetime(it.findtext("pubDate")).date()   # 날짜만 맞다 (평가 12판과 같다)
        title = _clean(it.findtext("title"))
        if published == day and title and title not in seen and not MARKET_TALK.search(title):
            seen.add(title)
            items.append({"title": title, "source": it.findtext("source"), "link": it.findtext("link")})
    return items


def classify(client, firm: Company, items: list[dict], spent: list, lock) -> list[dict]:
    """평가 12판의 제목 분류 지시문 그대로 묻는다. items 에 about·cat·imp·dir·kind·kw 를 붙여 돌려준다."""
    from eval.v12.classify import PRICE, SCHEMA, SYSTEM   # 평가에서 쓴 분류기와 같은 것을 쓴다

    out = []
    for start in range(0, len(items), 60):
        chunk = items[start:start + 60]
        content = f"회사: {firm.label} ({firm.stock_code})\n\n" + "\n".join(f"{i + 1}. {c['day']} {c['title']}" for i, c in enumerate(chunk))
        response = client.messages.create(
            model=MODEL, max_tokens=8000, system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
        if not response.model.startswith(MODEL):
            raise RuntimeError(f"다른 모델이 답함: {response.model}")
        with lock:
            spent[0] += sum((getattr(response.usage, k, 0) or 0) * p / 1e6 for k, p in PRICE.items())
        if response.stop_reason != "end_turn":
            continue
        text = next((b.text for b in response.content if b.type == "text"), "{\"items\": []}")
        for row in json.loads(text)["items"]:
            if 1 <= row["i"] <= len(chunk):
                out.append({**chunk[row["i"] - 1], **{k: row[k] for k in ("about", "cat", "imp", "dir", "kind", "kw")}})
    return out


def pick(client, db, day: date, spent: list, limit: int = LIMIT, cap: float = CAP) -> tuple[list[dict], dict]:
    """전날 뉴스를 모아 분류하고, 메모할 것을 고른다. (고른 것, 그날의 수)"""
    firms = universe(db, day)
    gathered: dict[int, list[dict]] = {}
    blocked = 0
    for firm in firms:
        items = headlines(firm.label, day)
        if items is None:
            blocked += 1
        elif items:
            gathered[firm.company_id] = [{**it, "day": day.isoformat()} for it in items[:PER_FIRM]]
        time.sleep(0.8 + random.random())
    by_id = {f.company_id: f for f in firms}
    lock, labeled = threading.Lock(), []
    with ThreadPoolExecutor(4) as pool:
        for firm_id, rows in zip(gathered, pool.map(lambda i: classify(client, by_id[i], gathered[i], spent, lock) if spent[0] <= cap else [], gathered)):
            labeled += [{**row, "company_id": firm_id, "company": by_id[firm_id].label, "code": by_id[firm_id].stock_code} for row in rows]
    core, chosen = choose(labeled, limit)
    counts = {"companies": len(firms), "with_news": len(gathered), "blocked": blocked, "titles": sum(map(len, gathered.values())), "core": len(core)}
    return chosen, counts


def choose(labeled: list[dict], limit: int = LIMIT) -> tuple[list[dict], list[dict]]:
    """핵심 뉴스(주인공이 그 회사, 산업, 중요도 MIN_IMP 이상)와, 그 가운데 메모할 것: 중요도 순, 한 회사 한 건, 같은 제목 한 번."""
    core = [r for r in labeled if r["about"] and r["cat"] == "산업" and (r["imp"] or 0) >= MIN_IMP]
    chosen, firms_taken, titles_taken, events_taken = [], set(), set(), []
    for row in sorted(core, key=lambda r: -r["imp"]):
        # 같은 사건이 여러 회사에서 잡힌다(HD한국조선해양의 '6301억', HD현대중공업의 '6300억원' 암모니아선).
        # 같은 종류에 금액이 2% 안으로 같으면 한 사건으로 본다. 연도(2030 등)는 금액으로 보지 않는다
        amounts = [float(n) for n in (m.replace(",", "").rstrip(".") for m in EVENT_NUMBER.findall(row["title"]))
                   if re.fullmatch(r"\d+(\.\d+)?", n) and not re.fullmatch(r"20\d\d", n)]
        same = any(kind == row.get("kind") and abs(a - b) <= 0.02 * max(a, b) for kind, b in events_taken for a in amounts)
        if row["company_id"] in firms_taken or row["title"] in titles_taken or same:
            continue
        firms_taken.add(row["company_id"])
        titles_taken.add(row["title"])
        events_taken += [(row.get("kind"), a) for a in amounts]
        chosen.append(row)
    return core, chosen[:limit]


def store(day: date, limit: int = LIMIT, cap: float = CAP):
    import anthropic

    Base.metadata.create_all(get_engine(), tables=[NewsMemo.__table__])
    with session() as db:
        if db.scalar(select(func.count()).select_from(NewsMemo).where(NewsMemo.news_date == day)):
            print(day, "이미 메모가 있다. 건너뛴다")
            return
        client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
        spent = [0.0]
        chosen, counts = pick(client, db, day, spent, limit, cap)
        print(day, counts, f"분류 ${spent[0]:.4f}", "고름", len(chosen), flush=True)
        for row in chosen:
            if spent[0] + 0.02 > cap:   # 메모 한 건은 많아야 $0.02 쯤이다. 다음 건이 상한을 넘을 수 있으면 멈춘다
                print("상한에 닿아 멈춘다", flush=True)
                break
            events, started = [], time.time()
            out = news_agent.run({"title": row["title"], "date": day.isoformat(), "company": row["company"], "code": row["code"]},
                                 client=client, check_numbers=True,
                                 on_event=lambda kind, value: events.append({"at": round((time.time() - started) * 1000), "event": {"kind": kind, **value}}))
            spent[0] += out["cost_usd"]
            db.add(NewsMemo(news_date=day, company_id=row["company_id"], title=row["title"][:300], link=(row.get("link") or "")[:1000] or None,
                            source=(row.get("source") or "")[:100] or None, events=events, result=news_agent.view(db, out), cost_usd=out["cost_usd"],
                            picked={k: row[k] for k in ("imp", "kind", "dir", "kw")} | counts, created_at=datetime.now()))
            db.commit()
            memo = out["memo"] or {}
            print(f"  {row['imp']:.2f} {row['company']} | {row['title'][:40]} | 조회 {len(out['tool_calls'])}번 | 관련 {len(memo.get('related', []))}곳"
                  f" | {out['model']} | 누적 ${spent[0]:.4f}", flush=True)
    print("쓴 돈", f"${spent[0]:.4f}")


def main():
    step = sys.argv[1]
    arg = lambda key, default: next((a.split("=", 1)[1] for a in sys.argv if a.startswith(f"--{key}=")), default)
    day = date.fromisoformat(arg("day", (datetime.now(KST).date() - timedelta(days=1)).isoformat()))
    if step == "count":   # 무료: 모아서 거른 제목 수만 센다 (분류 비용을 어림할 때)
        with session() as db:
            firms = universe(db, day)
            counts = [len(headlines(f.label, day) or []) for f in firms if not time.sleep(0.8 + random.random())]
        print(day, "회사", len(firms), "뉴스 있는 회사", sum(1 for n in counts if n), "제목", sum(counts), f"(회사당 {PER_FIRM}건까지)", sum(min(n, PER_FIRM) for n in counts))
    elif step == "store":
        store(day, int(arg("limit", LIMIT)), float(arg("cap", CAP)))
    elif step == "pick":
        import anthropic

        spent = [0.0]
        with session() as db:
            chosen, counts = pick(anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY")), db, day, spent, int(arg("limit", LIMIT)))
        print(day, counts, f"분류 ${spent[0]:.4f}")
        for row in chosen:
            print(f"  {row['imp']:.2f} {row['kind']} {row['company']} | {row['title']}")


if __name__ == "__main__":
    main()
