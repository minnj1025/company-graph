"""9판 기계 채점: 카드 종류와, 정답 값이 답의 글에 나왔는지.

- 이름: 띄어쓰기와 대소문자를 무시하고 답에 나오면 '나옴'
- 비율(%): 답의 % 수 가운데 ±0.01 안에 드는 것이 있으면 (부호는 보지 않는다. '0.23%p 줄었다'도 맞다)
- 원: 답의 금액('1조 1,079억', '150억 원', '1,668원') 가운데 상대 오차 0.5% 안에 드는 것이 있으면
- 주·건·명: 답의 수(쉼표를 뗀 정수) 가운데 같은 것이 있으면. '약 860만 주'처럼 줄여 쓴 것은 기계가 못 읽는다
'나오지 않음'은 빠짐이거나 다르게 적은 것이다. 틀림(다른 기간·보고자의 값)과 줄여 쓴 값은 사람이 답을 읽어 가린다 (results.md).

사용: python -m eval.v9.score [결과 폴더 이름]
"""
import glob
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def numbers(text: str) -> set[int]:
    return {int(n.replace(",", "")) for n in re.findall(r"(?<![\d.])\d[\d,]*(?![\d.])", text)}


def won(text: str) -> list[float]:
    from eval.v7.score import amounts
    plain = [float(n.replace(",", "")) for n in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*원", text)]
    return amounts(text) + plain


def percents(text: str) -> list[float]:
    return [float(p) for p in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]


def shown(calls: list[dict]) -> list[str]:
    return [v.get("kind") for c in calls if c["tool"] == "show_on_graph" and not c.get("error") for v in c["input"].get("views") or []]


def main(folder: str) -> None:
    value_hit = value_total = card_need = card_right = extra = 0
    for path in sorted(glob.glob(str(HERE / "gold" / "*.json"))):
        gold = json.load(open(path, encoding="utf-8"))
        answer = json.load(open(HERE / "agent" / folder / f"{gold['id']}.json", encoding="utf-8"))
        text = answer["answer"]
        nums, rates, flat = numbers(text), percents(text), squash(text)
        marks = []
        for value in gold.get("values", []):
            value_total += 1
            if value["unit"] == "이름":
                hit = squash(str(value["value"])) in flat
            elif value["unit"] == "원":
                hit = any(abs(m - float(value["value"])) <= 0.005 * abs(float(value["value"])) for m in won(text))
            elif value["unit"] == "%":
                hit = any(abs(r - abs(float(value["value"]))) <= 0.01 for r in rates)
            else:
                hit = abs(int(value["value"])) in nums
            value_hit += hit
            marks.append("○" if hit else "·")
        kinds = shown(answer["tool_calls"])
        want, ok_extra = set(gold["expected_kinds"]), set(gold.get("acceptable_kinds") or [])
        if want:
            card_need += 1
            ok = bool(set(kinds) & want) and set(kinds) <= want | ok_extra
            card_right += ok
        else:
            ok = not kinds
            extra += bool(kinds)
        print(gold["id"], "".join(marks) or "-", "카드", "○" if ok else "×", sorted(want), kinds, "|", gold["question"][:32])
    print(f"[{folder}] 값이 답에 나옴 {value_hit}/{value_total} | 카드가 필요한 문항 {card_right}/{card_need} | 필요 없는 문항에 띄움 {extra}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-5-5")
