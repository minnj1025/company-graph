"""평가 13판 자동 지표: 판·방식별 문항 수, 비용, 시간, 도구 호출, 결과에 없는 접수번호, 기대한 규칙을 읽었는가.

사용: python -m eval.v13.metrics   (eval/v13/metrics.json 에도 쓴다)
"""
import json
import statistics as st

from eval.run_agent import ROOT, v2_questions
from eval.v13.run import SETS, cost, folder

# 실행 전에 PLAN.md 에 적은 기대 규칙. 6판(카드 문항)은 세지 않는다
EXPECTED = {"v5": "why-questions", "v7": "financials", "v8": "holders-insiders", "v9": "capital-dividends"}
V3 = {"product": "topic-search", "issue": "topic-search", "company": "company-overview", "price_move": "price-moves"}


def expected(set_name: str, number: str) -> str | None:
    if set_name == "v3":
        kind = json.loads((ROOT / "v3" / "gold" / f"{number}.json").read_text(encoding="utf-8"))["type"]
        return V3.get(kind)
    return EXPECTED.get(set_name)


def loaded(result: dict) -> set[str]:
    return {n for c in result["tool_calls"] if c["tool"] == "read_skill" for n in (c["input"].get("names") or [])}


def main():
    out, hits = {}, {"hit": 0, "total": 0, "miss": []}
    for s in SETS:
        for arm in ("A", "B"):
            rows = []
            for number, *_ in v2_questions(s):
                path = folder(s, arm) / f"{number}.json"
                if path.exists():
                    rows.append((number, json.loads(path.read_text(encoding="utf-8"))))
            if not rows:
                continue
            out[f"{s}-{arm}"] = {
                "n": len(rows),
                "cost": round(sum(cost(r["usage"]) for _, r in rows), 4),
                "cost_per_q": round(st.mean(cost(r["usage"]) for _, r in rows), 5),
                "seconds": round(st.mean(r["seconds"] for _, r in rows), 1),
                "lookups": round(st.mean(sum(c["tool"] not in ("read_skill", "show_on_graph") for c in r["tool_calls"]) for _, r in rows), 2),
                "cited_not_in_results": sum(len(r["cited_not_in_results"]) for _, r in rows),
                "answer_chars": round(st.mean(len(r["answer"]) for _, r in rows)),
                "stopped": sum(r["stop_reason"] != "end_turn" for _, r in rows),
            }
            if arm == "B":
                for number, r in rows:
                    want = expected(s, number)
                    if want:
                        hits["total"] += 1
                        if want in loaded(r):
                            hits["hit"] += 1
                        else:
                            hits["miss"].append(f"{s} {number}: 기대 {want}, 읽음 {sorted(loaded(r)) or '없음'}")
    total = {}
    for arm in ("A", "B"):
        keys = [k for k in out if k.endswith(arm)]
        n = sum(out[k]["n"] for k in keys)
        if n:
            total[arm] = {"n": n, "cost": round(sum(out[k]["cost"] for k in keys), 4),
                          "cost_per_q": round(sum(out[k]["cost"] for k in keys) / n, 5),
                          "seconds": round(sum(out[k]["seconds"] * out[k]["n"] for k in keys) / n, 1),
                          "cited_not_in_results": sum(out[k]["cited_not_in_results"] for k in keys)}
    report = {"by_set": out, "total": total, "skill_hit": hits}
    (ROOT / "v13" / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in out.items():
        print(k, v)
    print("합계", total)
    print(f"기대한 규칙을 읽음 {hits['hit']}/{hits['total']}")
    for m in hits["miss"]:
        print("  ", m)


if __name__ == "__main__":
    main()
