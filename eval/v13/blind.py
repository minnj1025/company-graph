"""평가 13판 블라인드 채점 묶음: 3판·5판 문항마다 A·B 답을 섞어 '가'·'나'로 보인다. 어느 쪽이 어느 방식인지는 key.json 에만 둔다.

사용: python -m eval.v13.blind [AC]   → eval/v13/blind/ (A·B) 또는 blind-AC/ (A·C) 의 <묶음>.md, key.json
"""
import json
import random
import sys

from eval.run_agent import ROOT
from eval.v13.run import folder

BATCH = 13


def out_dir(arms: str):
    return ROOT / "v13" / ("blind" if arms == "AB" else f"blind-{arms}")


def main(arms: str = "AB"):
    OUT = out_dir(arms)
    rng = random.Random(13 if arms == "AB" else 14)
    OUT.mkdir(parents=True, exist_ok=True)
    key = {}
    for s in ("v3", "v5"):
        golds = []
        for path in sorted((ROOT / s / "gold").glob("*.json")):
            item = json.loads(path.read_text(encoding="utf-8"))
            if not item.get("skip") and item.get("type") != "followup":
                golds.append(item)
        for b in range(0, len(golds), BATCH):
            parts = []
            for item in golds[b:b + BATCH]:
                n = item["id"]
                answers = {arm: json.loads((folder(s, arm) / f"{n}.json").read_text(encoding="utf-8"))["answer"] for arm in arms}
                order = list(arms)
                rng.shuffle(order)
                key[f"{s}/{n}"] = {"가": order[0], "나": order[1]}
                gold = {k: v for k, v in item.items() if k not in ("id",)}
                parts.append(f"## {n}\n\n정답 자료:\n```json\n{json.dumps(gold, ensure_ascii=False, indent=1)}\n```\n\n"
                             f"### 가\n\n{answers[order[0]]}\n\n### 나\n\n{answers[order[1]]}\n")
            name = f"{s}-{b // BATCH + 1}"
            (OUT / f"{name}.md").write_text(f"# {name}\n\n" + "\n---\n\n".join(parts), encoding="utf-8")
            print(name, len(golds[b:b + BATCH]))
    (OUT / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "AB")
