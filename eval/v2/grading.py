"""평가 2판의 채점 묶음을 만들고, 채점 결과를 문항별로 모은다.

채점자는 어느 쪽이 쓴 답인지 모르고 판정한다. 그래서 묶음에는 답마다 뜻 없는 열쇠(key)만 붙이고,
열쇠가 어느 문항의 어느 쪽 답인지는 `grading/keys.json`에 따로 둔다.

실행: python -m eval.v2.grading bundle   → eval/v2/grading/*.json
      python -m eval.v2.grading collect  → eval/v2/grades.json
      결함을 고친 뒤 다시 돌린 답은 끝에 after 를 붙인다 (grading_after/, grades_after/, grades_after.json)
      웹 검색 기준선을 나중에 더 푼 문항은 끝에 more 를 붙인다 (grading_more/, grades_more/, 판정은 grades.json 에 더한다)
      그 뒤에 다시 돌린 답은 실행할 때 붙인 꼬리말을 적는다. 예: format → agent/<모델>-format/, grading_format/, grades_format/, grades_format.json
"""
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parent
AGENT_MODEL = "claude-haiku-5-5"
SEED = 20261008
BUNDLE_SIZE = 26


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def more_answers() -> list[dict]:
    """웹 검색 기준선의 답 가운데 아직 판정이 없는 것."""
    graded = read(ROOT / "grades.json")
    rows = []
    for path in sorted((ROOT / "baseline").glob("*.json")):
        gold = read(ROOT / "gold" / path.name)
        if "baseline" not in graded.get(gold["id"], {}):
            key = hashlib.sha256(f"{SEED}/baseline/{gold['id']}".encode()).hexdigest()[:10]
            rows.append({"key": key, "id": gold["id"], "side": "baseline", "answer": read(path)["answer"], "gold": gold})
    return rows


def answers(after: bool | str) -> list[dict]:
    """채점할 답 전부: Agent의 149개와 웹 검색 기준선의 32개. after 이면 고친 뒤에 다시 돌린 Agent의 149개.
    after 가 글자(꼬리말)이면 그 꼬리말로 다시 돌린 Agent의 답."""
    tag = after if isinstance(after, str) else "after"
    rows = []
    for path in sorted((ROOT / "gold").glob("*.json")):
        gold = read(path)
        if gold.get("skip"):
            continue
        sides = (((tag, ROOT / "agent" / (AGENT_MODEL + "-" + tag) / path.name),) if after else
                 (("agent", ROOT / "agent" / AGENT_MODEL / path.name), ("baseline", ROOT / "baseline" / path.name)))
        for side, file in sides:
            if file.exists():
                key = hashlib.sha256(f"{SEED}/{side}/{gold['id']}".encode()).hexdigest()[:10]
                rows.append({"key": key, "id": gold["id"], "side": side, "answer": read(file)["answer"], "gold": gold})
    return rows


def _suffix(after: bool | str, more: bool) -> str:
    return "_more" if more else f"_{after}" if isinstance(after, str) else "_after" if after else ""


def bundle(after: bool | str, more: bool = False):
    rows = more_answers() if more else answers(after)
    random.Random(SEED).shuffle(rows)
    out = ROOT / ("grading" + _suffix(after, more))
    out.mkdir(exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    (out / "keys.json").write_text(json.dumps({r["key"]: [r["id"], r["side"]] for r in rows}, indent=1), encoding="utf-8")
    for start in range(0, len(rows), BUNDLE_SIZE):
        items = [{"key": r["key"], "question": r["gold"]["question"], "as_of": r["gold"]["as_of"], "gold": r["gold"]["gold"],
                  "must_include": r["gold"]["must_include"], "must_not": r["gold"]["must_not"], "accept": r["gold"]["accept"],
                  "answer": r["answer"]} for r in rows[start:start + BUNDLE_SIZE]]
        (out / f"b{start // BUNDLE_SIZE + 1:02d}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"답 {len(rows)}개, 묶음 {-(-len(rows) // BUNDLE_SIZE)}개")


def collect(after: bool | str, more: bool = False):
    suffix = _suffix(after, more)
    keys = read(ROOT / f"grading{suffix}" / "keys.json")
    grades: dict[str, dict] = read(ROOT / "grades.json") if more else {}
    if more:
        suffix_out = ""
    else:
        suffix_out = suffix
    for path in sorted((ROOT / f"grades{suffix}").glob("b*.json")):
        for row in read(path):
            item, side = keys[row["key"]]
            grades.setdefault(item, {})[side] = {"verdict": row["verdict"], "reason": row["reason"]}
    missing = [key for key, (item, side) in keys.items() if side not in grades.get(item, {})]
    (ROOT / f"grades{suffix_out}.json").write_text(json.dumps(dict(sorted(grades.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"문항 {len(grades)}개, 판정이 빠진 답 {len(missing)}개")


if __name__ == "__main__":
    tag = (sys.argv[2:] or [""])[0]
    {"bundle": bundle, "collect": collect}[sys.argv[1]](after=tag if tag not in ("", "after", "more") else tag == "after", more=tag == "more")
