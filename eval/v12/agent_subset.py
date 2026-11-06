"""평가 12판에 덧붙인 시험: 뉴스 메모 Agent 가 고른 종목(A)을 일부 사건에서 B·C 와 견준다. README '덧붙인 시험' 참고.

  python -m eval.v12.agent_subset sample            최종 핵심 뉴스에서 100건을 뽑아 저장한다 (무료, run.py returns 뒤에)
  python -m eval.v12.agent_subset run --cap=1.5     뽑은 사건마다 Agent 를 한 번 돌린다 (유료, claude-haiku-5-5). 이어서 돌릴 수 있다
  python -m eval.v12.agent_subset returns           수익률을 잰다. agent_results.json 을 쓴다
"""
import hashlib
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
import threading

import anthropic

from company_graph import news_agent
from company_graph.config import secret
from eval.v12 import run as v12

OUT = v12.OUT
SAMPLE = OUT / "agent_sample.json"
MEMOS = OUT / "agent_memos"
N, SEED, SINCE = 100, 13, "2026-04-01"


def sample():
    events = json.loads((OUT / "events.json").read_text(encoding="utf-8"))["good"]
    pool = [e for e in events if e["date"] >= SINCE and e["groups"]["B"] and e["groups"]["C"]]
    picked = sorted(random.Random(SEED).sample(pool, min(N, len(pool))), key=lambda e: (e["entry"], e["code"]))
    SAMPLE.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    print("후보", len(pool), "뽑음", len(picked))


def _news(e: dict) -> dict:
    """Agent 에 넘길 뉴스: 본문 단계에서 그 기사로 확인된 본문이 있으면 함께."""
    bodies_path, second_path = OUT / "bodies" / f"{e['code']}.json", OUT / "labels2" / f"{e['code']}.json"
    body = json.loads(bodies_path.read_text(encoding="utf-8")).get(e["title"], {}) if bodies_path.exists() else {}
    second = json.loads(second_path.read_text(encoding="utf-8")).get(e["title"], {}) if second_path.exists() else {}
    news = {"title": e["title"], "date": e["date"], "company": e["name"], "code": e["code"]}
    if body.get("ok") and second.get("body_matches"):
        news.update(text=body["text"], kind=body.get("kind"))
    return news


def _path(e: dict):
    return MEMOS / f"{e['code']}_{hashlib.sha1((e['date'] + e['title']).encode()).hexdigest()[:10]}.json"


def run_agent(cap: float, limit: int | None):
    MEMOS.mkdir(parents=True, exist_ok=True)
    todo = [e for e in json.loads(SAMPLE.read_text(encoding="utf-8")) if not _path(e).exists()]
    if limit:
        todo = todo[:limit]
    print("남은 사건", len(todo), flush=True)
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    spent, lock, stop = [0.0], threading.Lock(), threading.Event()

    def one(e):
        if stop.is_set():
            return
        try:
            out = news_agent.run(_news(e), client=client)
        except anthropic.APIError as error:   # 실패한 사건은 다음 실행에서 다시
            print(e["code"], "실패", error, flush=True)
            return
        with lock:
            _path(e).write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
            spent[0] += out["cost_usd"]
            memo = out["memo"] or {}
            print(f"{e['date']} {e['name']} | 관련 {len(memo.get('related', []))} | ${out['cost_usd']:.4f} | 누적 ${spent[0]:.4f}", flush=True)
            if spent[0] > cap:
                print("상한을 넘어 멈춘다", flush=True)
                stop.set()
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(one, todo))
    print("쓴 돈", f"${spent[0]:.4f}")


def groups_a(e: dict, prices) -> tuple[list[str], list[str]]:
    path = _path(e)
    if not path.exists():
        return [], []
    memo = json.loads(path.read_text(encoding="utf-8"))["memo"] or {}
    ok = lambda r: (r.get("stock_code") and r["stock_code"] != e["code"] and r["stock_code"] in prices.rows[e["entry"]]
                    and prices.liquid(r["stock_code"], e["entry"]))
    rows = [r for r in memo.get("related", []) if ok(r)]
    return [r["stock_code"] for r in rows][:v12.SIZE], [r["stock_code"] for r in rows if r.get("closeness") == "직접"][:v12.SIZE]


def returns():
    prices = v12.Prices()
    events = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for e in events:
        e["groups"]["A"], e["groups"]["A직접"] = groups_a(e, prices)
    result = {"events": len(events), "memos": sum(1 for e in events if _path(e).exists()),
              "filled": {g: sum(1 for e in events if e["groups"][g]) for g in ("A", "A직접", "B", "C")}}
    for h in (3, 7):
        rows = {}
        for e in events:
            got = {g: v12.group_excess(prices, e["groups"][g], e["entry"], h) for g in ("A", "A직접", "B", "C")}
            for g, v in got.items():
                if v is not None:
                    rows.setdefault(g, []).append((e["entry"], v))
            for a, b in (("A", "B"), ("A", "C"), ("C", "B"), ("A직접", "B")):
                if got[a] is not None and got[b] is not None:
                    rows.setdefault(f"{a}-{b}", []).append((e["entry"], got[a] - got[b]))
        result[f"h{h}"] = {k: v12.clustered(v) for k, v in rows.items()}
    good = [bool((result[f"h{h}"].get("A-B", {}).get("mean_pct") or 0) > 0 and (result[f"h{h}"].get("A-B", {}).get("t") or 0) >= 2) for h in (3, 7)]
    result["say"] = ("이 표본에서는 Agent 가 키워드 방식보다 나았다" if all(good) else
                     "h=3, h=7 가운데 하나에서만 기준을 넘음" if any(good) else "이 표본에서는 차이를 보이지 못함")
    (v12.HERE / "agent_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    step = sys.argv[1]
    if step == "sample":
        sample()
    elif step == "run":
        cap = next((float(a.split("=")[1]) for a in sys.argv if a.startswith("--cap=")), 0.05)
        limit = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), None)
        run_agent(cap, limit)
    elif step == "returns":
        returns()
