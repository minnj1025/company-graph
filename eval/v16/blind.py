"""평가 16판 채점 자료(PLAN.md). Claude API를 부르지 않는다. 페이지 묶음은 15판(14판)의 것과 같고, 지분 답 묶음(S1)이 더해졌다.

사용: python -m eval.v16.blind          → eval/v16/blind/B<n>.md (페이지), eval/v16/blind/S1.md (지분 답)
      python -m eval.v16.blind tally    → 기계 지표와 eval/v16/grades/*.json 을 모아 eval/v16/results.json
지분 묶음의 두 표는 같은 로컬 DB에서 get_relations(받은 회사, equity, in, 상대=주주)의 reports 를 그대로 옮긴 것이다.
"""
import json
import sys
from pathlib import Path

from company_graph import agent_tools, query
from company_graph.db import session
from eval.v13.run import cost
from eval.v14.blind import BATCH, packet, results
from eval.v15.blind import v6_rows
from eval.v16.run import PAGES, STAKE, V6

HERE = Path(__file__).parent


def data() -> dict:
    return json.loads((HERE / "questions.json").read_text(encoding="utf-8"))


def questions() -> dict:
    d = data()
    return {q["id"]: q for group in ("page", "refuse", "stake") for q in d[group]}


def tables(db, q: dict, as_of: str) -> list[dict]:
    company, holder = (query.find_companies(db, q[k])[0] for k in ("company", "holder"))
    found = agent_tools.get_relations(db, company.company_id, as_of, "equity", "in", counterparty_id=holder.company_id)
    rows = [r for r in found["relations"] if r["subject"]["company_id"] == holder.company_id]
    return [rep for r in rows for rep in (r.get("reports") or [])]


def stake_packet(db, q: dict, result: dict, as_of: str) -> str:
    lines = [f"## {q['id']}", "", f"질문: {q['question']}", "", f"주주: {q['holder']} / 회사: {q['company']} / 조회 시점: {as_of}", "",
             "### 두 표", "", "| 표 | 낸 회사 | 지분율(%) | 주식 수 | 접수번호 |", "|---|---|---|---|---|"]
    lines += [f"| {t['table']} | {t['filer']} | {t['value']} | {t['shares']} | {t['rcept_no']} |" for t in tables(db, q, as_of)]
    lines += ["", "### 채팅 답", "", result["answer"] or "(빈 답)", ""]
    return "\n".join(lines)


def make():
    qs, d = questions(), data()
    made = [r for r in results(PAGES) if r.get("page_spec")]
    out = HERE / "blind"
    out.mkdir(exist_ok=True)
    with session() as db:
        parts = [packet(db, qs[r["number"]], r) for r in made]
        stakes = [stake_packet(db, qs[r["number"]], r, d["as_of"]) for r in results(STAKE)]
    for n in range(0, len(parts), BATCH):
        name = f"B{n // BATCH + 1}"
        (out / f"{name}.md").write_text(f"# {name}\n\n" + "\n---\n\n".join(parts[n:n + BATCH]), encoding="utf-8")
    (out / "S1.md").write_text("# S1\n\n" + "\n---\n\n".join(stakes), encoding="utf-8")
    print(f"페이지 {len(parts)}개, 묶음 {(len(parts) + BATCH - 1) // BATCH}개. 지분 답 {len(stakes)}개")


def tally():
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
    stake = {r["number"]: {"cost": round(cost(r["usage"]), 5), "cited_not_in_results": r["cited_not_in_results"],
                           "grade": grades.get(r["number"])} for r in results(STAKE)}
    page = [rows[n] for n in sorted(rows) if rows[n]["group"] == "page"]
    graded = [g for g in (x["grade"] for x in page if x["made"]) if g]
    v6 = {arm: v6_rows(folder) for arm, folder in V6.items()}
    right = {arm: sum(x["right"] for x in v) for arm, v in v6.items()}
    per_q = {arm: round(sum(x["cost"] for x in v) / (len(v) or 1), 5) for arm, v in v6.items()}
    n_page = len(page) or 1
    s_graded = [x["grade"] for x in stake.values() if x["grade"]]
    summary = {
        "1_pages_made": f"{sum(x['made'] for x in page)}/{len(page)} (기준 13 이상)",
        "2_fit": {k: sum(g["fit"] == k for g in graded) for k in ("적절", "일부", "부적절")} | {"graded": len(graded), "기준": "적절 12 이상, 부적절 0"},
        "3_wrong_fact_pages": f"{sum(bool(g['wrong_facts']) for g in graded)} (기준 0)",
        "4_unverified_pages": f"{sum(bool(x['unverified']) for x in page)} (기준 2 이하)",
        "5_refuse": {n: {"made": rows[n]["made"], "line": (rows[n]["grade"] or {}).get("lines", [])} for n in sorted(rows) if rows[n]["group"] == "refuse"},
        "6_v6": {"P2_right": right.get("P2"), "C_right": right.get("C"), "P2_save_calls": sum(bool(x["save_calls"]) for x in v6["P2"]),
                 "cost_per_q": per_q, "기준": "P2 save_page 0, P2 카드 18 이상이고 C보다 1 넘게 적지 않음, P2 문항당 비용이 C의 1.15배 이하"},
        "7_page_cost_per_q": f"{sum(x['cost'] for x in page) / n_page:.5f} (기준 0.012 이하)",
        "8_stake_wrong": f"{sum(bool(g['wrong_facts']) for g in s_graded)}/{len(s_graded)} (기준 0)",
        "stake_noted_difference": sum(bool(g.get("noted_difference")) for g in s_graded),
        "removed_count_pages": sum(bool(x["removed_counts"]) for x in page),
        "lines_in_pages": sum(bool(g.get("lines")) for g in graded),
        "total_cost": round(sum(x["cost"] for x in rows.values()) + sum(x["cost"] for x in stake.values())
                            + sum(x["cost"] for v in v6.values() for x in v), 4),
    }
    out = {"summary": summary, "pages": rows, "stake": stake, "v6": v6,
           "missing_grades": sorted([n for n, x in rows.items() if x["made"] and not x["grade"]] + [n for n, x in stake.items() if not x["grade"]])}
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1), "\n빠진 채점:", out["missing_grades"])


if __name__ == "__main__":
    tally() if sys.argv[1:] == ["tally"] else make()
