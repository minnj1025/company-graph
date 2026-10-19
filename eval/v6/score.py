"""6판 채점: Agent가 띄운 카드의 종류를 문항의 기대와 견준다 (기계적).

사용: python -m eval.v6.score [결과 폴더 이름]   (기본 claude-haiku-5-5)
카드는 처음에 show_view 도구로 띄웠고, 고친 뒤에는 show_on_graph 의 views 로 띄운다. 둘 다 센다.
"""
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def kinds(calls: list[dict]) -> tuple[list[str], list[str]]:
    shown, errors = [], []
    for call in calls:
        if call["tool"] == "show_view":
            shown.append(call["input"].get("kind"))
            if call.get("error"):
                errors.append(call["error"])
        elif call["tool"] == "show_on_graph" and not call.get("error"):   # 인자가 틀려 거절된 호출은 화면에 아무것도 띄우지 않았다
            shown += [view.get("kind") for view in call["input"].get("views") or []]
    return shown, errors


def main(folder: str) -> None:
    rows = []
    for path in sorted(glob.glob(str(HERE / "gold" / "*.json"))):
        gold = json.load(open(path, encoding="utf-8"))
        answer = json.load(open(HERE / "agent" / folder / f"{gold['id']}.json", encoding="utf-8"))
        shown, errors = kinds(answer["tool_calls"])
        want, extra = set(gold["expected_kinds"]), set(gold.get("acceptable_kinds") or [])
        right = (not shown) if not want else bool(set(shown) & want) and set(shown) <= want | extra
        rows.append((gold["id"], gold["question"][:34], sorted(want), shown, right, errors))
    need = [r for r in rows if r[2]]
    none = [r for r in rows if not r[2]]
    print(f"[{folder}] 카드가 필요한 질문 {len(need)}개 중 알맞게 고름 {sum(r[4] for r in need)} ({sum(r[4] for r in need) / len(need):.0%})")
    print(f"카드가 필요 없는 질문 {len(none)}개 중 카드를 띄운 것 {sum(1 for r in none if r[3])}")
    for r in rows:
        print("○" if r[4] else "×", r[0], r[1], "| 기대", r[2], "| 띄움", r[3], r[5] or "")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-5-5")
