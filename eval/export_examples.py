"""화면의 "Agent 예시"에 보여 줄 질문·답을 평가 결과에서 골라 한 파일로 만든다. Claude API를 부르지 않는다.

실행: python -m eval.export_examples   →  web/public/agent-examples.json
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
# (세트, 번호, 왜 골랐나). 잘한 것만 고르지 않고 답하지 못한 문항도 넣는다
PICKS = [
    ("practice", "Q05", "단건 조회"),
    ("practice", "Q06", "최대주주가 개인인 경우"),
    ("practice", "Q08", "빠짐없이 찾기 (도구 한 번으로 전 시장 조회)"),
    ("sealed", "S07", "시점: 정정 공시가 반영된 뒤의 값"),
    ("sealed", "S06", "시점: 같은 질문을 정정 전 시점으로"),
    ("practice", "Q21", "두 기업이 함께 지분을 가진 상장사"),
    ("sealed", "S18", "공시가 상대를 밝히지 않은 계약"),
    ("sealed", "S16", "공시가 없는 경우"),
    ("practice", "Q28", "매수·매도 판단은 거절"),
    ("sealed", "S10", "답하지 못한 문항 (이 시험 뒤에 DB를 고쳤다)"),
]


def main():
    out = []
    for kind, number, why in PICKS:
        path = ROOT / ("agent" if kind == "practice" else "sealed/agent") / "claude-haiku-5-5" / f"{number}.json"
        r = json.loads(path.read_text(encoding="utf-8"))
        out.append({"id": number, "kind": "봉인 시험" if kind == "sealed" else "연습 문항", "why": why,
                    "question": r["question"], "as_of": r["as_of"], "answer": r["answer"], "model": r["model"],
                    "seconds": r["seconds"], "tokens": sum(r["usage"].values()),
                    "tools": [{"name": c["tool"], "input": c["input"], "total": c["total"], "error": c["error"]}
                              for c in r["tool_calls"]]})
    target = ROOT.parent / "web" / "public" / "agent-examples.json"
    target.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(len(out), "개 →", target, target.stat().st_size, "바이트")


if __name__ == "__main__":
    main()
