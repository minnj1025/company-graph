"""평가 13판 블라인드 채점을 key.json 으로 풀어 A·B별로 센다.

사용: python -m eval.v13.tally   (eval/v13/grades/*.json → eval/v13/grades.json)
"""
import json
from collections import Counter

from eval.run_agent import ROOT

DIR = ROOT / "v13"


def main():
    key = json.loads((DIR / "blind" / "key.json").read_text(encoding="utf-8"))
    rows = {}
    for path in sorted((DIR / "grades").glob("*.json")):
        s = path.stem.split("-")[0]
        for number, by_label in json.loads(path.read_text(encoding="utf-8")).items():
            arms = key[f"{s}/{number}"]
            rows[f"{s}/{number}"] = {arms[label]: g for label, g in by_label.items()}
    missing = sorted(set(key) - set(rows))
    out = {"items": rows, "missing": missing, "summary": {}}
    for s in ("v3", "v5"):
        for arm in ("A", "B"):
            gs = [r[arm] for k, r in rows.items() if k.startswith(s)]
            c = Counter(g["verdict"] for g in gs)
            out["summary"][f"{s}-{arm}"] = {**c, "n": len(gs), "ok": c["correct"] + c["partial"],
                                            "forbidden": sum(bool(g.get("forbidden")) for g in gs)}
    changed = [f"{k}: A {r['A']['verdict']} → B {r['B']['verdict']}" for k, r in rows.items() if r["A"]["verdict"] != r["B"]["verdict"]]
    out["changed"] = changed
    (DIR / "grades.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in out["summary"].items():
        print(k, v)
    print("빠진 문항", missing or "없음")
    for line in changed:
        print("  ", line)


if __name__ == "__main__":
    main()
