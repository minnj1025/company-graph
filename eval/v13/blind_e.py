"""평가 13판 E(PLAN-E.md): 표현 검사에 걸린 문항마다 다시 쓰기 전(draft)과 뒤(answer)를 '가'·'나'로 섞어 보인다.

사용: python -m eval.v13.blind_e        → eval/v13/blind-E/<묶음>.md, key.json
      python -m eval.v13.blind_e tally  → eval/v13/grades-E/*.json 을 풀어 grades-E.json
"""
import json
import random
import sys
from collections import Counter

from eval.run_agent import ROOT
from eval.v13.run import folder

DIR = ROOT / "v13"
OUT = DIR / "blind-E"
RANK = {"correct": 3, "partial": 2, "unanswered": 1, "wrong": 0}


def fired():
    rows = []
    for s in ("v3", "v5"):
        for path in sorted(folder(s, "E").glob("*.json")):
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("wording", {}).get("retried"):
                rows.append((s, result))
    return rows


def make():
    rng = random.Random(16)
    OUT.mkdir(parents=True, exist_ok=True)
    key, parts = {}, []
    for s, result in fired():
        n = result["number"]
        gold = json.loads((ROOT / s / "gold" / f"{n}.json").read_text(encoding="utf-8"))
        texts = {"before": result["wording"]["draft"], "after": result["answer"]}
        order = ["before", "after"]
        rng.shuffle(order)
        key[f"{s}/{n}"] = {"가": order[0], "나": order[1]}
        gold = {k: v for k, v in gold.items() if k != "id"}
        parts.append(f"## {n}\n\n정답 자료:\n```json\n{json.dumps(gold, ensure_ascii=False, indent=1)}\n```\n\n"
                     f"### 가\n\n{texts[order[0]]}\n\n### 나\n\n{texts[order[1]]}\n")
    (OUT / "E-1.md").write_text("# E-1\n\n" + "\n---\n\n".join(parts), encoding="utf-8")
    (OUT / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(parts), "문항")


def tally():
    key = json.loads((OUT / "key.json").read_text(encoding="utf-8"))
    rows = {}
    for path in sorted((DIR / "grades-E").glob("*.json")):
        for number, by_label in json.loads(path.read_text(encoding="utf-8")).items():
            k = next(k for k in key if k.endswith("/" + number))
            rows[k] = {key[k][label]: g for label, g in by_label.items()}
    out = {"items": rows, "missing": sorted(set(key) - set(rows)), "summary": {}}
    for side in ("before", "after"):
        c = Counter(r[side]["verdict"] for r in rows.values())
        out["summary"][side] = {**c, "n": len(rows), "forbidden": sum(bool(r[side].get("forbidden")) for r in rows.values())}
    out["worse"] = [k for k, r in rows.items() if RANK[r["after"]["verdict"]] < RANK[r["before"]["verdict"]]]
    out["better"] = [k for k, r in rows.items() if RANK[r["after"]["verdict"]] > RANK[r["before"]["verdict"]]]
    (DIR / "grades-E.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "items"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    tally() if sys.argv[1:] == ["tally"] else make()
