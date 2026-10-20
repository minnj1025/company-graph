"""7판 정답 값을 get_financials 의 결과와 견준다 (모델 없이, 데이터만).

정답은 단일회사 주요계정 API(fnlttSinglAcnt)로 따로 받은 값이고, 도구는 다중회사 주요계정(fnlttMultiAcnt)으로 모은 DB에서 나온다.
Agent의 답을 채점하기 전에, 도구가 내주는 값 자체가 맞는지 본다.

사용: python -m eval.v7.data_check
"""
import glob
import json
import re
from pathlib import Path

from company_graph import agent_tools
from company_graph.db import session

HERE = Path(__file__).parent
KEYS = {"매출액": "revenue", "영업이익": "operating_income", "당기순이익": "net_income"}


def pick(found: dict, label: str):
    """정답의 이름표를 도구 결과의 칸으로 옮긴다."""
    year = re.search(r"(20\d\d)년", label)
    if "부채비율" in label:
        return found["balance"]["latest"]["debt_ratio_pct"]
    if "유동비율" in label:
        return found["balance"]["latest"]["current_ratio_pct"]
    quarter = re.search(r"(\d)분기", label)
    if quarter:
        row = next((q for q in found["quarters"] if q["period"] == f"{year.group(1)} {quarter.group(1)}분기"), None)
    elif "상반기" in label:
        row = next((y for y in found["years"] if y["period"] == f"{year.group(1)} 상반기 누적"), None)
    else:
        row = next((y for y in found["years"] if y["period"] == f"{year.group(1)} 연간"), None)
    if row is None:
        return None
    if "영업이익률" in label:
        return row.get("operating_margin_pct")
    key = next(v for k, v in KEYS.items() if k in label)
    if "증감률" in label:
        return row.get(f"{key}_yoy_pct")
    return None if row.get(key) is None else row[key] * 1e8


def main() -> None:
    right = total = 0
    with session() as db:
        for path in sorted(glob.glob(str(HERE / "gold" / "*.json"))):
            gold = json.load(open(path, encoding="utf-8"))
            for value in gold.get("values", []):
                total += 1
                name = gold["company"][0]
                if len(gold["company"]) > 1:
                    name = next((c for c in gold["company"] if value["label"].startswith(c[:2]) or value["label"].startswith({"NAVER": "네이버"}.get(c, c))), name)
                company = agent_tools.call(db, "find_company", {"name": name})["candidates"][0]
                found = agent_tools.financial_data(db, company["company_id"], gold["as_of"], value["basis"])
                mine = pick(found, value["label"])
                if value["unit"] == "%":
                    ok = mine is not None and abs(mine - value["value"]) <= 0.1
                else:
                    ok = mine is not None and abs(mine - value["value"]) <= 0.005 * abs(value["value"])
                right += ok
                print("○" if ok else "×", gold["id"], value["label"], "| 정답", value["value"], "| 도구", mine)
    print(f"{right}/{total}")


if __name__ == "__main__":
    main()
