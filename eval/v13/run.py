"""평가 13판: 같은 문항을 A(지금 지시문)와 B(ASK_SKILLS=1)로 번갈아 돌린다. Claude API를 부른다(유료, claude-haiku-5-5).

실행: python -m eval.v13.run [--cap 1.5] [--arms AB]
- C(ASK_SKILLS=2, 원인·평가 규칙은 지시문에 둠)는 PLAN-C.md: python -m eval.v13.run --arms C --cap 0.6
- 문항마다 A 다음 B. 같은 날 같은 DB로 잰다
- 결과는 eval/<판>/agent/claude-haiku-5-5-v13A, -v13B. 이미 있는 답은 건너뛴다
- 누적 비용이 상한을 넘으면 멈춘다(이번에 돌리는 방식의 이미 쓴 답 비용까지 센다)
"""
import argparse
import json
import os
import time

from sqlalchemy import text

from company_graph import agent
from company_graph.db import session
from eval.run_agent import ROOT, v2_questions

MODEL = "claude-haiku-5-5"
PRICE = {"input_tokens": 0.10, "output_tokens": 0.50, "cache_read_input_tokens": 0.01, "cache_creation_input_tokens": 0.125}  # $/백만 토큰 (Haiku)
SETS = ["v3", "v5", "v6", "v7", "v8", "v9"]
ARMS = {"A": "0", "B": "1", "C": "2"}


def cost(usage: dict) -> float:
    return sum(usage.get(k, 0) * p for k, p in PRICE.items()) / 1e6


def db_ready(wait: int = 600) -> bool:
    """모델을 부르기 전에 DB가 살아 있는지 본다. 꺼져 있으면 wait 초까지 기다린다(DB가 꺼진 채로 돈을 쓰지 않게)."""
    end = time.time() + wait
    while True:
        try:
            with session() as db:
                db.execute(text("select 1"))
            return True
        except Exception:
            if time.time() > end:
                return False
            time.sleep(10)


def folder(set_name: str, arm: str):
    return ROOT / set_name / "agent" / f"{MODEL}-v13{arm}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap", type=float, default=1.5)
    parser.add_argument("--arms", default="AB")
    args = parser.parse_args()
    arms = {a: ARMS[a] for a in args.arms}
    items = [(s, *row) for s in SETS for row in v2_questions(s)]
    spent = 0.0
    for s, number, *_ in items:
        for arm in arms:
            path = folder(s, arm) / f"{number}.json"
            if path.exists():
                spent += cost(json.loads(path.read_text(encoding="utf-8"))["usage"])
    print(f"문항 {len(items)}개, 이미 쓴 비용 ${spent:.4f}", flush=True)
    for s, number, question, gold, as_of in items:
        for arm, flag in arms.items():
            path = folder(s, arm) / f"{number}.json"
            if path.exists():
                continue
            if spent >= args.cap:
                print(f"상한 ${args.cap} 에 닿아 멈춤 (누적 ${spent:.4f})", flush=True)
                return
            if not db_ready():
                print(f"DB에 닿지 않아 멈춤 (누적 ${spent:.4f})", flush=True)
                return
            os.environ["ASK_SKILLS"] = flag
            try:
                result = agent.answer(question, model=MODEL, as_of=as_of)
            finally:
                os.environ.pop("ASK_SKILLS", None)
            result["number"], result["gold"], result["arm"] = number, gold, arm
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            spent += cost(result["usage"])
            skills = [c["input"].get("names") for c in result["tool_calls"] if c["tool"] == "read_skill"]
            print(f"{s} {number} {arm} {result['seconds']}초 도구 {len(result['tool_calls'])}회 "
                  f"없는 근거 {len(result['cited_not_in_results'])} 규칙 {skills or '-'} 누적 ${spent:.4f}", flush=True)
    print(f"끝. 누적 ${spent:.4f}", flush=True)


if __name__ == "__main__":
    main()
