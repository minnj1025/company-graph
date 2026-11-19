"""평가 15판 채점 자료(PLAN.md). Claude API를 부르지 않는다. 14판의 것과 같고, 경로와 기준, 6판 두 방식 견주기만 다르다.

사용: python -m eval.v15.blind          → eval/v15/blind/<묶음>.md (페이지마다 질문, 요청 항목, 페이지, 조회 결과)
      python -m eval.v15.blind tally    → 기계 지표와 eval/v15/grades/*.json 을 모아 eval/v15/results.json
조회 결과는 답을 쓸 때 Agent가 받은 것과 같다: 같은 로컬 DB, 같은 입력으로 도구를 다시 부르고 화면용(_로 시작하는) 값은 뺀다.
페이지 블록은 서버가 세어서 하는 문장을 지운 뒤의 것(실제로 저장될 것)이다.
"""
import json
import sys
from pathlib import Path

from company_graph.db import session
from eval.run_agent import ROOT
from eval.v6.score import kinds
from eval.v13.run import cost
from eval.v14.blind import BATCH, packet, results
from eval.v15.run import PAGES, V6

HERE = Path(__file__).parent


def questions() -> dict:
    data = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    return {q["id"]: q for group in ("page", "refuse") for q in data[group]}


def make():
    qs = questions()
    made = [r for r in results(PAGES) if r.get("page_spec")]
    out = HERE / "blind"
    out.mkdir(exist_ok=True)
    with session() as db:
        parts = [packet(db, qs[r["number"]], r) for r in made]
    for n in range(0, len(parts), BATCH):
        name = f"B{n // BATCH + 1}"
        (out / f"{name}.md").write_text(f"# {name}\n\n" + "\n---\n\n".join(parts[n:n + BATCH]), encoding="utf-8")
    print(f"페이지 {len(parts)}개, 묶음 {(len(parts) + BATCH - 1) // BATCH}개")


def v6_rows(folder: Path) -> list[dict]:
    gold = {g["id"]: g for g in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "v6" / "gold").glob("*.json"))}
    rows = []
    for r in results(folder):
        g = gold[r["number"]]
        shown, _ = kinds(r["tool_calls"])
        want, extra = set(g["expected_kinds"]), set(g.get("acceptable_kinds") or [])
        right = (not shown) if not want else bool(set(shown) & want) and set(shown) <= want | extra
        rows.append({"number": r["number"], "shown": shown, "save_calls": sum(c["tool"] == "save_page" for c in r["tool_calls"]),
                     "right": right, "cost": cost(r["usage"])})
    return rows


def tally():
    qs = questions()
    grades = {}
    for path in sorted((HERE / "grades").glob("*.json")):
        grades.update(json.loads(path.read_text(encoding="utf-8")))
    rows = {}
    for r in results(PAGES):
        tries = [c for c in r["tool_calls"] if c["tool"] == "save_page"]
        spec = r.get("page_spec")
        rows[r["number"]] = {"group": r["group"], "made": bool(spec), "save_calls": len(tries),
                             "refused": sum(bool(c["error"]) for c in tries), "unverified": (spec or {}).get("unverified", []),
                             "removed_counts": (spec or {}).get("removed_counts", []),
                             "cited_not_in_results": r["cited_not_in_results"], "cost": round(cost(r["usage"]), 5),
                             "seconds": r["seconds"], "grade": grades.get(r["number"])}
    page = [rows[n] for n in sorted(rows) if rows[n]["group"] == "page"]
    graded = [g for g in (x["grade"] for x in page if x["made"]) if g]
    v6 = {arm: v6_rows(folder) for arm, folder in V6.items()}
    right = {arm: sum(x["right"] for x in v) for arm, v in v6.items()}
    per_q = {arm: round(sum(x["cost"] for x in v) / (len(v) or 1), 5) for arm, v in v6.items()}
    n_page = len(page) or 1
    summary = {
        "1_pages_made": f"{sum(x['made'] for x in page)}/{len(page)} (기준 13 이상)",
        "2_fit": {k: sum(g["fit"] == k for g in graded) for k in ("적절", "일부", "부적절")} | {"graded": len(graded), "기준": "적절 12 이상, 부적절 0"},
        "3_wrong_fact_pages": f"{sum(bool(g['wrong_facts']) for g in graded)} (기준 0)",
        "4_unverified_pages": f"{sum(bool(x['unverified']) for x in page)} (기준 2 이하)",
        "5_refuse": {n: {"made": rows[n]["made"], "line": (rows[n]["grade"] or {}).get("lines", [])} for n in sorted(rows) if rows[n]["group"] == "refuse"},
        "6_v6": {"P2_right": right.get("P2"), "C_right": right.get("C"), "P2_save_calls": sum(bool(x["save_calls"]) for x in v6["P2"]),
                 "cost_per_q": per_q, "기준": "P2 save_page 0, P2 카드 18 이상이고 C보다 1 넘게 적지 않음, P2 문항당 비용이 C의 1.15배 이하"},
        "7_page_cost_per_q": f"{sum(x['cost'] for x in page) / n_page:.5f} (기준 0.012 이하)",
        "removed_count_pages": sum(bool(x["removed_counts"]) for x in page),
        "lines_in_pages": sum(bool(g.get("lines")) for g in graded),
        "total_cost": round(sum(x["cost"] for x in rows.values()) + sum(x["cost"] for v in v6.values() for x in v), 4),
    }
    out = {"summary": summary, "pages": rows, "v6": v6, "missing_grades": sorted(n for n, x in rows.items() if x["made"] and not x["grade"])}
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1), "\n빠진 채점:", out["missing_grades"])


if __name__ == "__main__":
    tally() if sys.argv[1:] == ["tally"] else make()
