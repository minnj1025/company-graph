"""평가 13판 블라인드 채점을 key.json 으로 풀어 방식별로 센다.

사용: python -m eval.v13.tally [AC]   (grades/ → grades.json, A·C 는 grades-AC/ → grades-AC.json)
"""
import json
import sys
from collections import Counter

from eval.run_agent import ROOT

DIR = ROOT / "v13"


def main(arms: str = "AB"):
    tag = "" if arms == "AB" else f"-{arms}"
    key = json.loads((DIR / f"blind{tag}" / "key.json").read_text(encoding="utf-8"))
    rows = {}
    for path in sorted((DIR / f"grades{tag}").glob("*.json")):
        s = path.stem.split("-")[0]
        for number, by_label in json.loads(path.read_text(encoding="utf-8")).items():
            label_arm = key[f"{s}/{number}"]
            rows[f"{s}/{number}"] = {label_arm[label]: g for label, g in by_label.items()}
    missing = sorted(set(key) - set(rows))
    out = {"items": rows, "missing": missing, "summary": {}}
    for s in ("v3", "v5"):
        for arm in arms:
            gs = [r[arm] for k, r in rows.items() if k.startswith(s)]
            c = Counter(g["verdict"] for g in gs)
            out["summary"][f"{s}-{arm}"] = {**c, "n": len(gs), "ok": c["correct"] + c["partial"],
                                            "forbidden": sum(bool(g.get("forbidden")) for g in gs)}
    x, y = arms
    changed = [f"{k}: {x} {r[x]['verdict']} → {y} {r[y]['verdict']}" for k, r in rows.items() if r[x]["verdict"] != r[y]["verdict"]]
    out["changed"] = changed
    (DIR / f"grades{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in out["summary"].items():
        print(k, v)
    print("빠진 문항", missing or "없음")
    for line in changed:
        print("  ", line)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "AB")
