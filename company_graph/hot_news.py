"""연관 급등·급락 하나마다 그날의 기사를 찾아 "기사는 무엇 때문이라고 전했나"를 한 줄로 적는다.

공시에는 무엇을 파는 회사인지만 있고 그날 무슨 일이 있었는지는 없다. 그래서 하루에 몇 건뿐인 뚜렷한 종목군만 골라
Claude 에게 웹 검색을 시켜 기사를 찾게 한다. 방문자가 몇 명이든 하루에 드는 값이 같다(유료: 종목군 하나에 검색 3번 안쪽과 Haiku 한 번).

지어내지 않게 하는 장치: 이유를 적으려면 검색 결과에 실제로 나온 기사의 주소를 대야 한다. 대지 못하면 "기사를 찾지 못함"으로 둔다.
기사는 저장하지 않는다. 우리가 쓴 한 줄과 기사의 제목·주소만 남긴다.

실행: python -m company_graph.hot_news store                 아직 이유가 없는 날을 채운다 (store 시작 끝)
      python -m company_graph.hot_news try 2026-09-30         하루를 돌려 찍어 보기만 한다 (저장하지 않음)
"""
import json
import re
import sys
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from .config import secret
from .db import HotDay, session

MODEL = "claude-haiku-5-5"
SEARCHES = 3
MAX_TOKENS = 8000   # 검색 사이사이의 생각도 여기서 나간다. 2,000으로 두었을 때는 답을 적기 전에 끊겨 찾은 기사를 버렸다
RELATIONS = ("계열", "지분", "공급계약")
GROUP, SINGLE, NONE = "group", "single", "none"   # 종목군을 다룬 기사 / 한 종목만 다룬 기사 / 못 찾음

SYSTEM = """한국 주식시장에서 어느 날 함께 크게 움직인 종목들을 받습니다. 웹 검색으로 그날이나 다음 날의 한국어 기사를 찾아, 기사가 그 움직임을 무엇 때문이라고 전했는지 확인합니다.

지킬 것
- 검색은 종목 이름과 "특징주", "급등", "강세"(내린 날이면 "급락", "약세"), 날짜를 섞어 합니다. 연결 고리로 받은 말(제품 이름)은 실제 이유와 다를 수 있으니 그것만으로 찾지 않습니다.
- 그 거래일이나 바로 다음 날(장 마감 뒤의 정리 기사)에 나온 기사만 근거로 씁니다. 다른 날의 기사, 날짜를 알 수 없는 글은 근거가 아닙니다.
- 받은 종목 가운데 둘 이상을 함께 다뤘거나 그 종목들이 든 업종·테마 전체가 움직였다고 한 기사가 있으면 found 는 "group" 입니다.
- 한 종목의 사정만 다룬 기사뿐이면 "single" 이고, reason 에 어느 종목의 이야기인지 밝힙니다.
- 맞는 기사가 없으면 "none" 입니다. 아는 것으로 짐작해 이유를 만들지 않습니다.
- reason 은 한 문장, 80자 안쪽입니다. 기사의 문장을 옮기지 말고 "…기대감에 강관주가 일제히 올랐다고 전했다"처럼 기사가 전한 내용으로 씁니다. 주가 전망이나 매수·매도 의견은 쓰지 않습니다.
- urls 에는 근거로 삼은 기사의 주소를 검색 결과에 나온 그대로 한두 개 적습니다.

마지막에 아래 JSON 한 덩어리만 적습니다. 다른 말은 붙이지 않습니다.
{"found": "group" | "single" | "none", "reason": "…", "urls": ["…"]}"""


def question(group: dict, day: date, down: bool) -> str:
    products = [why for why in group["why"] if why not in RELATIONS and "_" not in why]
    relations = [why for why in group["why"] if why in RELATIONS]
    link = " · ".join(products) + (" / " if products and relations else "") + ("·".join(relations) + " 관계" if relations else "")
    members = ", ".join(f"{m['name']} {m['change']:+.1f}%" for m in group["members"][:6])
    return (f"거래일: {day.isoformat()} ({'내린' if down else '오른'} 날)\n종목과 등락률: {members}\n"
            f"공시에서 찾은 연결 고리: {link}\n다음 날: {(day + timedelta(days=1)).isoformat()}")


def _json(text: str) -> dict | None:
    for chunk in reversed(re.findall(r"\{[^{}]*\}", text, flags=re.S)):
        try:
            value = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(value, dict) and "found" in value:
            return value
    return None


def _published(age: str | None, today: date) -> date | None:
    """검색 결과가 알려 준 기사의 나이("24 days ago")로 어림한 날짜. 읽지 못하는 꼴이면 None."""
    found = re.fullmatch(r"(\d+|an?) (hour|day|week)s? ago", (age or "").strip())
    if not found:
        return None
    count = 1 if found.group(1) in ("a", "an") else int(found.group(1))
    return today - timedelta(days=count * {"hour": 0, "day": 1, "week": 7}[found.group(2)])


def read_answer(content: list, day: date | None = None, today: date | None = None) -> dict:
    """모델의 답을 저장할 모양으로. 검색 결과에 없던 주소나 그 거래일 무렵의 것이 아닌 기사를 근거로 댔으면 버리고,
    근거가 하나도 남지 않으면 못 찾은 것으로 한다."""
    seen, text, searches = {}, "", 0
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "web_search_tool_result":
            searches += 1
            results = block.content if isinstance(block.content, list) else []
            for result in results:
                if getattr(result, "url", None):
                    seen[result.url] = {"title": result.title, "url": result.url, "age": getattr(result, "page_age", None)}
        elif kind == "text":
            text += block.text
    answer = _json(text) or {}
    sources = [seen[url] for url in answer.get("urls") or [] if url in seen]
    if day:   # 주 단위로만 알려 주는 오래된 기사는 앞뒤로 한 주의 여유를 둔다
        near = lambda at, loose: at is None or day - timedelta(days=2 + loose) <= at <= day + timedelta(days=4 + loose)
        sources = [s for s in sources if near(_published(s["age"], today or date.today()), 7 if "week" in (s["age"] or "") else 0)]
    sources = [{"title": s["title"], "url": s["url"]} for s in sources[:2]]
    found = answer.get("found") if answer.get("found") in (GROUP, SINGLE) and sources else NONE
    return {"found": found, "reason": str(answer.get("reason") or "").strip()[:160] if found != NONE else "", "sources": sources, "searches": searches}


def explain(client, group: dict, day: date, down: bool, model: str = MODEL) -> dict:
    messages = [{"role": "user", "content": question(group, day, down)}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": SEARCHES,
              "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}}]
    content, used = [], [0, 0]
    for _ in range(3):   # 서버가 검색을 돌리다 쉬면(pause_turn) 이어서 부른다
        response = client.messages.create(model=model, max_tokens=MAX_TOKENS, system=SYSTEM, tools=tools, messages=messages)
        content += response.content
        used = [used[0] + response.usage.input_tokens, used[1] + response.usage.output_tokens]
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": content}]
    if response.stop_reason == "max_tokens":   # 답을 다 적지 못했다. 못 찾은 것으로 남기지 않고 다음에 다시 찾게 한다
        raise RuntimeError("답이 끊겼습니다 (max_tokens)")
    return {**read_answer(content, day), "tokens": used}


def _client():
    import anthropic

    return anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))


def _wanted(payload: dict):
    """이유를 찾을 종목군: 양쪽의 뚜렷한 것."""
    for down, side in ((False, payload), (True, payload.get("down") or {})):
        for group in side.get("groups", []):
            if group["grade"] == "뚜렷함":
                yield down, group


def store(db, start: date | None = None, end: date | None = None, redo: str = "") -> tuple[int, int]:
    """redo: "" 이면 아직 찾아보지 않은 것만, "none" 이면 못 찾았던 것도 다시, "all" 이면 전부 다시."""
    client, done, searches = _client(), 0, 0
    rows = select(HotDay).order_by(HotDay.trade_date)
    if start:
        rows = rows.where(HotDay.trade_date >= start)
    if end:
        rows = rows.where(HotDay.trade_date <= end)
    for row in db.scalars(rows):
        changed = False
        for down, group in _wanted(row.payload):
            if "news" in group and not (redo == "all" or (redo == "none" and group["news"]["found"] == NONE)):
                continue
            try:
                news = explain(client, group, row.trade_date, down)
            except Exception as error:   # 한 건이 안 되어도 나머지는 한다. 이 건은 다음 실행 때 다시 찾는다
                print(row.trade_date, group["why"][0], "실패:", str(error)[:80], flush=True)
                continue
            group["news"] = {key: news[key] for key in ("found", "reason", "sources")}
            done, searches, changed = done + 1, searches + news["searches"], True
            print(row.trade_date, "하락" if down else "상승", group["why"][0], "→", news["found"], news["reason"], flush=True)
        if changed:
            flag_modified(row, "payload")
            db.commit()
    return done, searches


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with session() as db:
        if args and args[0] == "try":
            row = db.get(HotDay, date.fromisoformat(args[1]))
            client = _client()
            for down, group in _wanted(row.payload):
                print(question(group, row.trade_date, down))
                print(json.dumps(explain(client, group, row.trade_date, down), ensure_ascii=False, indent=1), "\n")
        elif args and args[0] == "store":
            if len(args) >= 3:
                start, end = (date.fromisoformat(a) for a in args[1:3])
            else:   # 날마다 도는 일: 가장 최근 닷새만 본다. 지난 날을 한꺼번에 채우려면 기간을 준다
                end = db.scalar(select(HotDay.trade_date).order_by(HotDay.trade_date.desc()).limit(1))
                start = end - timedelta(days=7)
            done, searches = store(db, start, end, redo=next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--redo=")), ""))
            print(f"종목군 {done}건, 검색 {searches}번")


if __name__ == "__main__":
    main()
