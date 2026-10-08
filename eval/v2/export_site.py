"""평가 2판의 문항, 정답, 두 쪽의 답과 판정을 화면이 읽는 파일 하나로 모은다.

실행: python -m eval.v2.export_site  → web/public/eval-v2.json
"""
import json
from pathlib import Path

from eval.v2.grading import AGENT_MODEL, ROOT, read

OUT = ROOT.parents[1] / "web" / "public" / "eval-v2.json"
BASELINE_MODEL = "claude-opus-5-5"

TYPES = {
    "T01": "정정 사이 시점의 값",
    "T02": "정정으로 바뀐 것",
    "T03": "해지된 계약",
    "T04": "정정·철회된 취득·처분 결정",
    "T05": "자회사 일을 모회사가 공시",
    "T06": "한 해의 계약을 빠짐없이",
    "T07": "이 회사를 상대로 한 계약",
    "T08": "상대 비공개 · 공시 없음",
    "T09": "취득·처분 결정의 세부",
    "T10": "최대주주와 특수관계인",
    "T11": "최대주주의 변화",
    "T12": "조건에 맞는 출자",
    "T13": "계열회사의 상장 구분",
    "T14": "두 단계 질문",
    "T15": "특수관계자를 상대로 한 계약",
    "T16": "답하면 안 되는 질문",
}


def main():
    grades = read(ROOT / "grades.json")
    after_grades = read(ROOT / "grades_after.json")   # 이 시험으로 찾은 결함을 고친 뒤 다시 돌린 결과
    usage = read(ROOT / "baseline_usage.json")
    items = []
    for path in sorted((ROOT / "gold").glob("*.json")):
        gold = read(path)
        if gold.get("skip"):
            continue
        item = {key: gold[key] for key in ("id", "type", "question", "as_of", "gold", "evidence", "why_hard")}
        ran = read(ROOT / "agent" / AGENT_MODEL / path.name)
        item["agent"] = {
            **grades[gold["id"]]["agent"], "answer": ran["answer"], "seconds": ran["seconds"], "tokens": sum(ran["usage"].values()),
            "tools": [{"name": call["tool"], "input": call["input"], "total": call["total"], "error": call["error"]} for call in ran["tool_calls"]],
            "unverified": ran["cited_not_in_results"],
        }
        again = read(ROOT / "agent" / (AGENT_MODEL + "-after") / path.name)
        item["after"] = {
            **after_grades[gold["id"]]["after"], "answer": again["answer"], "seconds": again["seconds"], "tokens": sum(again["usage"].values()),
            "tools": [{"name": call["tool"], "input": call["input"], "total": call["total"], "error": call["error"]} for call in again["tool_calls"]],
            "unverified": again["cited_not_in_results"],
        }
        base = ROOT / "baseline" / path.name
        if base.exists():
            answer = read(base)
            item["baseline"] = {
                **grades[gold["id"]]["baseline"], "answer": answer["answer"], "confidence": answer["confidence"], "sources": answer["sources"],
                "searches": answer["searches"], "fetches": answer["fetches"], "fetch_failures": answer["fetch_failures"],
                "opened_original": answer["opened_dart_original"], **usage[gold["id"]],
            }
        items.append(item)
    value = {"agent_model": AGENT_MODEL, "baseline_model": BASELINE_MODEL, "types": TYPES, "items": items}
    OUT.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    print(f"{len(items)}문항, 기준선 {sum('baseline' in i for i in items)}문항, {OUT.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
