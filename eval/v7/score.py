"""7판 기계 채점: 카드 종류와, 정답 값이 답의 글에 나왔는지.

- 카드: 6판과 같다 (show_on_graph 의 views).
- 값: 답에서 '89조 4,924억', '4,752억 원', '157.14%' 같은 수를 모두 뽑아, 정답 값과 허용 오차(원 0.5%, %는 ±0.1%p) 안에 드는 수가 있으면 '나옴'.
  '나오지 않음'은 빠짐이거나 틀림이다. 다른 기간·기준의 값을 적었는지(틀림)는 사람이 답을 읽어 가린다 (results.md).

사용: python -m eval.v7.score [결과 폴더 이름]
"""
import glob
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
NUM = r"\d[\d,]*(?:\.\d+)?"


def amounts(text: str) -> list[float]:
    """답의 금액(원)을 모두 뽑는다."""
    out = []
    for jo, eok in re.findall(rf"({NUM})\s*조(?:\s*({NUM})\s*억)?", text):
        out.append(float(jo.replace(",", "")) * 1e12 + (float(eok.replace(",", "")) * 1e8 if eok else 0))
    # '조' 뒤에 붙은 억은 위에서 이미 셌다. 단독 억만 남도록 조 묶음을 지운 글에서 다시 센다
    alone = re.sub(rf"{NUM}\s*조(?:\s*{NUM}\s*억)?", " ", text)
    out += [float(e.replace(",", "")) * 1e8 for e in re.findall(rf"({NUM})\s*억", alone)]
    return out


def percents(text: str) -> list[float]:
    return [float(p.replace(",", "")) * (-1 if sign == "-" or sign == "−" else 1)
            for sign, p in re.findall(rf"([-−+]?)\s*({NUM})\s*%", text)]


def shown(calls: list[dict]) -> list[str]:
    return [v.get("kind") for c in calls if c["tool"] == "show_on_graph" and not c.get("error") for v in c["input"].get("views") or []]


def main(folder: str) -> None:
    value_hit = value_total = 0
    card_need = card_right = extra_cards = 0
    for path in sorted(glob.glob(str(HERE / "gold" / "*.json"))):
        gold = json.load(open(path, encoding="utf-8"))
        answer = json.load(open(HERE / "agent" / folder / f"{gold['id']}.json", encoding="utf-8"))
        text = answer["answer"]
        money, rates = amounts(text), percents(text)
        marks = []
        for value in gold.get("values", []):
            value_total += 1
            if value["unit"] == "%":
                hit = any(abs(abs(p) - abs(value["value"])) <= 0.1 for p in rates)
            else:
                hit = any(abs(m - value["value"]) <= 0.005 * abs(value["value"]) for m in money)
            value_hit += hit
            marks.append("○" if hit else "·")
        kinds = shown(answer["tool_calls"])
        want, extra = set(gold["expected_kinds"]), set(gold.get("acceptable_kinds") or [])
        if want:
            card_need += 1
            ok = bool(set(kinds) & want) and set(kinds) <= want | extra
            card_right += ok
        else:
            ok = not kinds
            extra_cards += bool(kinds)
        print(gold["id"], "".join(marks) or "-", "카드", "○" if ok else "×", sorted(want), kinds, "|", gold["question"][:30])
    print(f"[{folder}] 값이 답에 나옴 {value_hit}/{value_total} | 카드가 필요한 문항 {card_right}/{card_need} | 필요 없는 문항에 띄움 {extra_cards}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-5-5")
