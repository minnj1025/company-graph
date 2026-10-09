"""평가 3판의 Agent 실행과 채점 묶음.

실행: python -m eval.v3.grading run [--model claude-haiku-5-5] [--tag=-r2]   → eval/v3/agent/<모델><꼬리말>/<id>.json (P, I, C 문항. Claude API, 유료)
      python -m eval.v3.grading bundle                           → eval/v3/grading/b01.json … (어느 문항인지 가린 열쇠만 붙인다)
      python -m eval.v3.grading collect                          → eval/v3/grades.json
이어지는 대화(F) 문항의 답은 `python -m eval.v3.followups` 가 만든다.
"""
import hashlib
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
MODEL = "claude-haiku-5-5"
SEED = 20261009
BUNDLE_SIZE = 15


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def golds() -> list[dict]:
    return [g for g in (read(p) for p in sorted((ROOT / "gold").glob("*.json"))) if not g.get("skip")]


def run(model: str, tag: str = ""):
    from company_graph import agent

    out = ROOT / "agent" / (model + tag)
    out.mkdir(parents=True, exist_ok=True)

    def one(gold: dict) -> str:
        result = agent.answer(gold["question"], model=model, as_of=date.fromisoformat(gold["as_of"]))
        result["number"] = gold["id"]
        (out / f"{gold['id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return f"{gold['id']} {result['seconds']}초 도구 {len(result['tool_calls'])}회 없는 근거 {len(result['cited_not_in_results'])}"

    todo = [g for g in golds() if g["type"] != "followup" and not (out / f"{g['id']}.json").exists()]
    with ThreadPoolExecutor(4) as pool:
        for line in pool.map(one, todo):
            print(line, flush=True)


def bundle(model: str):
    """model 에 꼬리말이 붙어 있으면(claude-haiku-5-5-after) 그 폴더의 답만 묶어 grading-after/ 에 둔다."""
    tag = model[len(MODEL):]
    rows = []
    for gold in golds():
        file = ROOT / "agent" / model / f"{gold['id']}.json"
        if not file.exists():
            continue
        answer = read(file)
        key = hashlib.sha256(f"{SEED}/{tag}/{gold['id']}".encode()).hexdigest()[:10] if tag else hashlib.sha256(f"{SEED}/{gold['id']}".encode()).hexdigest()[:10]
        item = {"key": key, "question": gold["question"], "as_of": gold["as_of"], "gold": gold["gold"], "must_include": gold["must_include"],
                "must_not": gold["must_not"], "accept": gold["accept"], "others": gold.get("others", []), "answer": answer["answer"]}
        if gold["type"] == "followup":   # 둘째 질문만 판정하지만, 무엇을 가리키는지 알도록 앞선 대화를 같이 보여 준다
            item["earlier"] = {"question": answer["first"]["question"], "answer": answer["first"]["answer"]}
        rows.append((key, gold["id"], item))
    random.Random(SEED).shuffle(rows)
    out = ROOT / ("grading" + tag)
    out.mkdir(exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    (out / "keys.json").write_text(json.dumps({key: number for key, number, _ in rows}, indent=1), encoding="utf-8")
    for start in range(0, len(rows), BUNDLE_SIZE):
        (out / f"b{start // BUNDLE_SIZE + 1:02d}.json").write_text(
            json.dumps([item for _, _, item in rows[start:start + BUNDLE_SIZE]], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"답 {len(rows)}개, 묶음 {-(-len(rows) // BUNDLE_SIZE)}개")


def collect(tag: str = ""):
    keys = read(ROOT / ("grading" + tag) / "keys.json")
    grades = {}
    for path in sorted((ROOT / ("grades" + tag)).glob("b*.json")):
        for row in read(path):
            grades[keys[row["key"]]] = {"verdict": row["verdict"], "reason": row["reason"], "extra": row.get("extra", [])}
    (ROOT / f"grades{tag}.json").write_text(json.dumps(dict(sorted(grades.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"문항 {len(grades)}개, 판정이 빠진 답 {len(keys) - len(grades)}개")


if __name__ == "__main__":
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else MODEL
    tag = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--tag=")), "")
    {"run": lambda: run(model, tag), "bundle": lambda: bundle(model), "collect": lambda: collect(model[len(MODEL):])}[sys.argv[1]]()
