"""'뉴스 메모' 화면의 미리 돌려 둔 예시를 만든다. 방문자가 예시를 누르면 Agent를 부르지 않고 그때 서버가 보낸 사건을 다시 보여 준다.

  python -m eval.memo_examples real --cap=0.10      예시 뉴스마다 news_agent 를 한 번 돌려 기록한다 (유료, claude-haiku-5-5)
  python -m eval.memo_examples real --redo --limit=1 조회 계획 없이 녹화한 예시를 다시 녹화한다 (유료)
  python -m eval.memo_examples fake <메모 JSON>...  지난 시험 메모의 조회 순서를 가짜 모델로 다시 돌린다 (무료, 화면 확인용. 배포하지 않는다)

결과: web/public/memo-examples.json (real) 또는 data/cache/memo-examples-fake.json (fake).
기사 본문은 넣지 않는다(저작권). 제목, 날짜, 사건, 메모만 남는다.
"""
import json
import pathlib
import sys
import time
from datetime import date, timedelta
from types import SimpleNamespace as NS

from company_graph import api, news_agent
from company_graph.config import CACHE_DIR
from company_graph.db import session

# 화면에 보일 예시. 평가 12판에서 모은 뉴스 제목(Google 뉴스) 가운데 업종이 겹치지 않게 골랐다. 기사 본문은 넘기지 않는다
REAL = [
    {"title": "포스코퓨처엠, 삼성SDI와 6조 규모 LFP 양극재 공급계약", "day": "2026-10-06"},
    {"title": "삼성전기, 메모리 기판 3605억원 수주…장기계약 7건째", "day": "2026-10-19"},
    {"title": "일진전기, 영국에 1872억원 송전망 케이블 공급", "day": "2026-10-02"},
    {"title": "0.59점 차이로…한화오션, 차기구축함 우선협상대상자 선정", "day": "2026-07-02"},
]


def record(news: dict, client=None) -> dict:
    """news_agent 를 돌리며 사건과 걸린 시각을 적는다. 화면의 /api/memo/stream 과 같은 순서로 만든다."""
    day = date.fromisoformat(news["day"])
    found, events, started = api._Found(day - timedelta(days=1)), [], time.time()
    out = news_agent.run({"title": news["title"], "date": news["day"]}, client=client, check_numbers=True, plan=True,
                         on_event=lambda kind, value: events.append({"at": round((time.time() - started) * 1000), "event": {"kind": kind, **value}}),
                         on_result=found.take)
    with session() as db:
        result = api.memo_json(db, out, found)
    return {"news": {"title": news["title"], "day": news["day"]}, "events": events, "result": result, "cost_usd": out["cost_usd"]}


class Replay:
    """지난 메모의 조회를 한 번에 하나씩 부르고, 마지막에 그 메모를 내는 가짜 모델."""

    def __init__(self, saved: dict):
        self.turns = [[("tool_use", c["tool"], c["input"])] for c in saved["tool_calls"]]
        memo = {k: v for k, v in saved["memo"].items()}
        memo["related"] = [{k: v for k, v in r.items() if k not in ("name", "stock_code")} for r in memo["related"]]
        memo["subjects"] = [{k: v for k, v in s.items() if k not in ("name", "stock_code")} for s in memo["subjects"]]
        self.turns.append([("tool_use", "write_memo", memo)])
        self.messages = NS(create=self.create)

    def create(self, **request):
        time.sleep(0.4)
        blocks = [NS(type=kind, id=f"t{len(self.turns)}", name=name, input=data) for kind, name, data in self.turns.pop(0)]
        return NS(model=news_agent.MODEL, stop_reason="tool_use", content=blocks,
                  usage=NS(input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def main():
    step = sys.argv[1]
    if step == "fake":
        out = []
        for path in sys.argv[2:]:
            saved = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
            out.append(record({"title": saved["news"]["title"], "day": saved["news"]["date"]}, client=Replay(saved)))
        (CACHE_DIR / "memo-examples-fake.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
        print("가짜 예시", len(out))
    elif step == "real":
        import anthropic
        from company_graph.config import secret
        cap = next((float(a.split("=")[1]) for a in sys.argv if a.startswith("--cap=")), 0.05)
        limit = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), None)
        client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
        target = pathlib.Path("web/public/memo-examples.json")
        done = json.loads(target.read_text(encoding="utf-8")) if target.exists() else []
        # --redo: 조회 계획(plan) 없이 녹화한 예시를 다시 녹화해 그 자리에 바꿔 넣는다
        redo = "--redo" in sys.argv
        have = {e["news"]["title"] for e in done if not redo or any(x["event"]["kind"] == "plan" for x in e["events"])}
        spent = 0.0
        for news in [n for n in REAL if n["title"] and n["title"] not in have][:limit]:
            example = record(news, client=client)
            spent += example.pop("cost_usd")
            at = next((i for i, e in enumerate(done) if e["news"]["title"] == news["title"]), None)
            if at is None:
                done.append(example)
            else:
                done[at] = example
            target.write_text(json.dumps(done, ensure_ascii=False, default=str), encoding="utf-8")
            memo = example["result"]["memo"] or {}
            print(f"{news['day']} {news['title'][:30]} | 조회 {len(example['result']['tools'])}번 | 관련 {len(memo.get('related', []))}곳"
                  f" | 모델 {example['result']['model']} | 누적 ${spent:.4f}", flush=True)
            if spent > cap:
                print("상한을 넘어 멈춘다")
                break


if __name__ == "__main__":
    main()
