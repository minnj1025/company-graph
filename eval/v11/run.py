"""11판: 답의 말투. 같은 12문항을 지금 지시문(A)과 새 말투(B, ANSWER_STYLE=2)로 돌려 둔다 (유료: Haiku, 답 하나에 약 $0.004~0.006).

문항은 이미 정답이 있는 평가 5~9판에서 골랐다(README.md). 정확도는 각 판의 채점 기준으로, 말투는 A·B 를 가린 블라인드 채점으로 본다.
사용: python -m eval.v11.run [A|B|both] [F01,D08,...|all]
결과: eval/v11/answers/<A|B>/<문항>.json. 이미 있으면 건너뛴다.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
MODEL = "claude-haiku-5-5"   # 제품이 쓰는 모델. agent.MODEL(Opus)을 기본으로 두었다가 큰 비용이 나간 적이 있다
PRICE = {"input_tokens": 0.1, "output_tokens": 0.5, "cache_read_input_tokens": 0.01, "cache_creation_input_tokens": 0.125}   # 달러 / 100만 토큰
QUESTIONS = [("F01", "v7"), ("D08", "v9"), ("H04", "v8"), ("F15", "v7"), ("D14", "v9"), ("D16", "v9"),
             ("H16", "v8"), ("V05", "v6"), ("F13", "v7"), ("W1-02", "v5"), ("D18", "v9"), ("V17", "v6")]


def cost(usage: dict) -> float:
    return sum(usage.get(k, 0) * p for k, p in PRICE.items()) / 1e6


def main(styles: list[str], only: set[str]) -> None:
    total = 0.0
    for number, source in QUESTIONS:
        if only and number not in only:
            continue
        gold = json.loads((ROOT / source / "gold" / f"{number}.json").read_text(encoding="utf-8"))
        for style in styles:
            out = HERE / "answers" / style / f"{number}.json"
            if out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            os.environ["ANSWER_STYLE"] = "2" if style == "B" else "1"
            from company_graph import agent   # 지시문은 부를 때마다 환경 변수를 읽는다
            result = agent.answer(gold["question"], model=MODEL, as_of=date.fromisoformat(gold["as_of"]))
            result.update({"number": number, "source": source, "style": style, "cost_usd": round(cost(result["usage"]), 5)})
            total += result["cost_usd"]
            out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{number} {style} {result['model']} ${result['cost_usd']} {result['seconds']}초 도구 {len(result['tool_calls'])}회 "
                  f"없는 근거 {len(result['cited_not_in_results'])}", flush=True)
    print(f"합계 ${total:.4f}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] != "all" else set()
    main(["A", "B"] if which == "both" else [which], only)
