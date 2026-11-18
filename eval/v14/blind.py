"""평가 14판 채점 자료(PLAN.md). Claude API를 부르지 않는다.

사용: python -m eval.v14.blind          → eval/v14/blind/<묶음>.md (페이지마다 질문, 요청 항목, 페이지, 조회 결과)
      python -m eval.v14.blind tally    → 기계 지표와 eval/v14/grades/*.json 을 모아 eval/v14/results.json
조회 결과는 답을 쓸 때 Agent가 받은 것과 같다: 같은 로컬 DB, 같은 입력으로 도구를 다시 부르고 화면용(_로 시작하는) 값은 뺀다.
"""
import json
import sys
from pathlib import Path

from company_graph import agent_tools
from company_graph.db import session
from eval.run_agent import ROOT
from eval.v6.score import kinds
from eval.v13.run import MODEL, cost

HERE = Path(__file__).parent
PAGES = HERE / "agent" / f"{MODEL}-P"
V6 = ROOT / "v6" / "agent" / f"{MODEL}-v14P"
BATCH = 4
SKIP = {"read_skill", "save_page", "show_on_graph", "show_view"}


def questions() -> dict:
    data = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    return {q["id"]: q for group in ("page", "refuse") for q in data[group]}


def results(folder: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def packet(db, q: dict, result: dict) -> str:
    page = result["page_spec"]
    lines = [f"## {result['number']}", "", f"질문: {result['question']}", ""]
    if q.get("asks"):
        lines += ["요청 항목: " + " / ".join(q["asks"]), ""]
    lines += [f"페이지 제목: {page['title']}", f"조회 시점: {page['as_of']}", "", "### 페이지 블록", ""]
    for i, block in enumerate(page["blocks"], 1):
        if block["type"] == "text":
            lines += [f"{i}. 글", "", block["text"], ""]
        else:
            card = agent_tools.call(db, "show_view", {k: v for k, v in block.items() if k != "type"})
            lines += [f"{i}. 카드 {block['kind']} — {card.get('shown') or card.get('error')}", "",
                      f"카드 요약(서버가 DB에서 채움): {card.get('summary') or '-'}", ""]
    lines += ["### 그 답의 조회 결과", ""]
    for call in result["tool_calls"]:
        if call["tool"] in SKIP or call["error"]:
            continue
        out = agent_tools.call(db, call["tool"], dict(call["input"]))
        body = json.dumps({k: v for k, v in out.items() if not k.startswith("_")}, ensure_ascii=False, default=str)
        lines += [f"- {call['tool']} {json.dumps(call['input'], ensure_ascii=False)}", "", "```json", body, "```", ""]
    return "\n".join(lines)


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


def tally():
    qs = questions()
    pages = {r["number"]: r for r in results(PAGES)}
    grades = {}
    for path in sorted((HERE / "grades").glob("*.json")):
        grades.update(json.loads(path.read_text(encoding="utf-8")))
    rows = {}
    for number, r in pages.items():
        tries = [c for c in r["tool_calls"] if c["tool"] == "save_page"]
        spec = r.get("page_spec")
        rows[number] = {"group": r["group"], "made": bool(spec), "save_calls": len(tries),
                        "refused": sum(bool(c["error"]) for c in tries), "unverified": (spec or {}).get("unverified", []),
                        "cited_not_in_results": r["cited_not_in_results"], "cost": round(cost(r["usage"]), 5),
                        "seconds": r["seconds"], "grade": grades.get(number)}
    page = [rows[n] for n in sorted(rows) if rows[n]["group"] == "page"]
    graded = [g for g in (x["grade"] for x in page if x["made"]) if g]
    refuse = [rows[n] for n in sorted(rows) if rows[n]["group"] == "refuse"]

    v6_gold = {json.load(open(p, encoding="utf-8"))["id"]: json.load(open(p, encoding="utf-8")) for p in (ROOT / "v6" / "gold").glob("*.json")}
    v6 = []
    for r in results(V6):
        gold = v6_gold[r["number"]]
        shown, _ = kinds(r["tool_calls"])
        want, extra = set(gold["expected_kinds"]), set(gold.get("acceptable_kinds") or [])
        right = (not shown) if not want else bool(set(shown) & want) and set(shown) <= want | extra
        v6.append({"number": r["number"], "save_calls": sum(c["tool"] == "save_page" for c in r["tool_calls"]),
                   "right": right, "cost": cost(r["usage"])})

    n_page = len(page) or 1
    summary = {
        "1_pages_made": f"{sum(x['made'] for x in page)}/{len(page)} (기준 13 이상)",
        "2_fit": {k: sum(g["fit"] == k for g in graded) for k in ("적절", "일부", "부적절")} | {"graded": len(graded), "기준": "적절 12 이상, 부적절 0"},
        "3_wrong_fact_pages": f"{sum(bool(g['wrong_facts']) for g in graded)} (기준 0)",
        "4_unverified_pages": f"{sum(bool(x['unverified']) for x in page)} (기준 2 이하)",
        "5_refuse": {n: {"made": x["made"], "line": (x["grade"] or {}).get("lines", [])} for n, x in zip(sorted(k for k in rows if rows[k]["group"] == "refuse"), refuse)},
        "6_v6": {"n": len(v6), "save_calls": sum(bool(x["save_calls"]) for x in v6), "right": sum(x["right"] for x in v6),
                 "cost_per_q": round(sum(x["cost"] for x in v6) / (len(v6) or 1), 5), "기준": "save_page 0, 카드 19 이상, $0.005336 이하"},
        "7_page_cost_per_q": f"{sum(x['cost'] for x in page) / n_page:.5f} (기준 0.012 이하)",
        "lines_in_pages": sum(bool(g.get("lines")) for g in graded),
        "total_cost": round(sum(x["cost"] for x in rows.values()) + sum(x["cost"] for x in v6), 4),
    }
    out = {"summary": summary, "pages": rows, "v6": v6, "missing_grades": sorted(n for n, x in rows.items() if x["made"] and not x["grade"])}
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1), "\n빠진 채점:", out["missing_grades"])


if __name__ == "__main__":
    tally() if sys.argv[1:] == ["tally"] else make()
