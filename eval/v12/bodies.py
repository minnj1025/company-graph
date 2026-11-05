"""평가 12판 2단계: 제목으로 고른 후보 기사의 본문을 가져온다 (무료).

- Google 뉴스 주소를 언론사 원래 주소로 바꾼 뒤(페이지에 실린 서명값으로 batchexecute 를 부른다) 그 페이지를 받아
  trafilatura 로 본문만 뽑는다. 앞 1,500자만 남긴다.
- 본문은 data/cache/v12/bodies/{종목코드}.json 에만 둔다. 저장소에 넣지 않는다(저작권).
- 본문을 못 뽑으면 언론사 페이지의 요약문(og:description)을 쓴다(kind 로 구분). 못 가져온 기사는 사유를 남기고, 판정은 제목만으로 한 것으로 표시된다(classify.py body).
- 막히면(429·503) 오래 쉬고, 계속 막히면 멈춘다. 우회하지 않는다.

실행: python -m eval.v12.bodies [--workers=4]
"""
import json
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
import trafilatura

from company_graph.config import CACHE_DIR

OUT = CACHE_DIR / "v12"
KEEP = 1500
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}


class Blocked(Exception):
    pass


def _get(url: str, **kw) -> requests.Response:
    for attempt in range(3):
        r = requests.request(kw.pop("method", "GET"), url, headers={**UA, **kw.pop("headers", {})}, timeout=20, **kw)
        if r.status_code not in (429, 503):
            return r
        time.sleep(90 * (attempt + 1))
    raise Blocked(url)


def resolve(link: str) -> str | None:
    art = urllib.parse.urlparse(link).path.split("/")[-1]
    page = _get(f"https://news.google.com/rss/articles/{art}").text
    sg, ts = re.search(r'data-n-a-sg="([^"]+)"', page), re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and ts):
        return None
    req = ["Fbv4je", f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{art}",{ts.group(1)},"{sg.group(1)}"]']
    r = _get("https://news.google.com/_/DotsSplashUi/data/batchexecute", method="POST", data={"f.req": json.dumps([[req]])},
             headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    m = re.search(r'\[\\"garturlres\\",\\"(.*?)\\"', r.text)
    return m.group(1).encode().decode("unicode_escape") if m else None


def body(link: str) -> dict:
    try:
        url = resolve(link)
        if not url:
            return {"ok": False, "why": "주소를 풀지 못함"}
        html = requests.get(url, headers=UA, timeout=20).text
        text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
        text = " ".join(text.split())
        if len(text) >= 150:
            return {"ok": True, "url": url, "kind": "본문", "text": text[:KEEP]}
        # 본문을 못 뽑으면 언론사가 페이지에 넣어 둔 요약문(og:description)을 쓴다
        meta = re.search(r"""<meta[^>]+(?:property|name)=["'](?:og:description|description)["'][^>]+content=["']([^"']+)""", html)
        lead = " ".join(meta.group(1).split()) if meta else ""
        if len(lead) >= 60:
            return {"ok": True, "url": url, "kind": "요약문", "text": lead[:KEEP]}
        return {"ok": False, "why": "본문이 짧거나 없음", "url": url}
    except Blocked:
        raise
    except Exception as e:   # 언론사 페이지마다 실패 모양이 달라 사유만 남긴다
        return {"ok": False, "why": type(e).__name__}


def candidates() -> list[tuple[str, str, str]]:
    """1단계 결과에서 후보: 주인공이 이 회사, 산업, 중요도 0.5 이상. (종목코드, 제목, 주소)"""
    links = {}
    for folder in (OUT / "news").iterdir():
        for path in folder.glob("*.json"):
            for item in json.loads(path.read_text(encoding="utf-8")):
                title = re.sub(r"\s+-\s+[^-]{1,40}$", "", item["title"] or "").strip()
                links.setdefault((folder.name, title), item["link"])
    out = []
    for path in (OUT / "labels").glob("*.json"):
        for chunk in json.loads(path.read_text(encoding="utf-8")).values():
            for r in chunk:
                if r.get("about") and r.get("cat") == "산업" and (r.get("imp") or 0) >= 0.5 and (path.stem, r["title"]) in links:
                    out.append((path.stem, r["title"], links[(path.stem, r["title"])]))
    return out


def main():
    workers = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--workers=")), 4)
    folder = OUT / "bodies"
    folder.mkdir(parents=True, exist_ok=True)
    done = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")}
    todo = [c for c in candidates() if c[1] not in done.get(c[0], {})]
    print("후보", len(todo), flush=True)
    lock, stop = threading.Lock(), threading.Event()
    count = [0, 0]

    def one(job):
        code, title, link = job
        if stop.is_set():
            return
        try:
            got = body(link)
        except Blocked:
            print("계속 막힘: 멈춘다", flush=True)
            stop.set()
            return
        with lock:
            done.setdefault(code, {})[title] = got
            count[0] += 1
            count[1] += got["ok"]
            if count[0] % 50 == 0:
                for c, rows in done.items():
                    (folder / f"{c}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                print(f"{count[0]}건, 본문 있음 {count[1]}", flush=True)
        time.sleep(0.5)
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, todo))
    for c, rows in done.items():
        (folder / f"{c}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"끝: {count[0]}건, 본문 있음 {count[1]}")


if __name__ == "__main__":
    main()
