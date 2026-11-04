"""11판 블라인드 채점 파일을 만든다. 문항마다 A·B 답의 순서를 섞어 '답 1', '답 2'로 적고, 어느 쪽이 무엇인지는 key.json 에만 둔다.
채점자에게는 pairs.md 만 준다. 무료 자동 지표(metrics.json)도 함께 계산한다.

사용: python -m eval.v11.blind
"""
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
from eval.v11.run import QUESTIONS  # noqa: E402

FORBIDDEN = re.compile(r"때문에|긍정적|부정적|부담이|실시간|목표가|사도 됩|매수하")
INTERNAL = re.compile(r"company_id|rcept_no|as_of|pct_after|corrects|treasury_cancellations|원장")
RAW_WON = re.compile(r"(?<![\d,])\d{10,}(?![\d,])")
TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
RECEIPT = re.compile(r"(?<!\d)20\d{12}(?!\d)")


def body(answer: str) -> str:
    return answer.split("<<이어서>>")[0].strip()


def metrics(result: dict) -> dict:
    text = body(result["answer"])
    lines = [line for line in text.splitlines() if line.strip()]
    views = [c for c in result.get("tool_calls", []) if c.get("tool") == "show_on_graph" and (c.get("input") or {}).get("views")]
    last = lines[-1] if lines else ""
    return {"chars": len(text), "output_tokens": result["usage"]["output_tokens"],
            "has_table": bool(TABLE.search(text)), "has_views": bool(views),
            "views": sorted({v.get("kind") if isinstance(v, dict) else v for c in views for v in c["input"]["views"]}, key=str),
            "db_subject": text.count("이 DB") + text.count("DB에"), "internal_names": len(INTERNAL.findall(text)),
            "raw_won": len(RAW_WON.findall(RECEIPT.sub("", text))),   # 접수번호(14자리)를 뺀 10자리 넘는 날 숫자
            "forbidden": FORBIDDEN.findall(text), "receipts": len(set(RECEIPT.findall(text))),
            "ends_with_unknown": bool(re.search(r"없습니다|알 수 없|모으지 않|확인되지 않|나오지 않", last)),
            "cited_not_in_results": result.get("cited_not_in_results", [])}


def main() -> None:
    rng = random.Random(20261104)
    key, out, table = {}, ["# 11판 블라인드 채점: 답의 말투", "",
                           "문항마다 같은 질문에 대한 답 두 개가 있습니다. 어느 쪽이 어떤 지시문으로 만든 것인지는 알려 주지 않습니다.", ""], {}
    for number, source in QUESTIONS:
        a = json.loads((HERE / "answers" / "A" / f"{number}.json").read_text(encoding="utf-8"))
        b = json.loads((HERE / "answers" / "B" / f"{number}.json").read_text(encoding="utf-8"))
        table[number] = {"A": metrics(a), "B": metrics(b)}
        first, second = (("A", a), ("B", b)) if rng.random() < 0.5 else (("B", b), ("A", a))
        key[number] = {"답 1": first[0], "답 2": second[0]}
        out += [f"## {number} ({source})", "", f"질문: {a['question']}", f"조회 시점: {a['as_of']}", f"정답 파일: eval/{source}/gold/{number}.json", ""]
        for label, (_, result) in (("답 1", first), ("답 2", second)):
            views = metrics(result)["views"]
            out += [f"### {label}" + (f" (화면에 카드도 띄움: {', '.join(map(str, views))})" if views else ""), "", body(result["answer"]), ""]
    (HERE / "blind").mkdir(exist_ok=True)
    (HERE / "blind" / "pairs.md").write_text("\n".join(out), encoding="utf-8")
    (HERE / "blind" / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "metrics.json").write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    total = {side: {k: sum((m[side][k] if isinstance(m[side][k], (int, bool)) else len(m[side][k])) for m in table.values())
                    for k in ("chars", "output_tokens", "has_table", "db_subject", "internal_names", "raw_won", "forbidden", "receipts",
                              "ends_with_unknown", "cited_not_in_results")} for side in "AB"}
    both = {side: sum(1 for m in table.values() if m[side]["has_table"] and m[side]["has_views"]) for side in "AB"}
    views_same = sum(1 for m in table.values() if m["A"]["views"] == m["B"]["views"])
    print(json.dumps(total, ensure_ascii=False, indent=1))
    print("카드와 표가 함께 있는 답:", both, "| 카드 종류가 A와 같은 문항:", views_same, "/", len(table))


if __name__ == "__main__":
    main()
