"""평가 16판(PLAN.md): 지분 도구 수정과 세는 문장 검사 수정 뒤의 Agent 페이지(P2), 그리고 두 표 값이 다른 지분 질문(S, 지금 기본 C).
Claude API를 부른다(유료, claude-haiku-5-5).

실행: python -m eval.v16.run [--cap 0.50] [--only P31]
- 페이지 요청 15문항(P31~P45), 페이지로 만들면 안 되는 요청 2문항(R05, R06): P2
- 6판 카드 20문항: P2 와 C 두 번
- 지분 질문 10문항(S01~S10): C
- 결과: eval/v16/agent/claude-haiku-5-5-P2/, eval/v16/agent/claude-haiku-5-5-C/, eval/v6/agent/claude-haiku-5-5-v16P2/, -v16C/. 이미 있는 답은 건너뛴다
- 누적 비용이 상한을 넘으면 멈춘다(이미 쓴 답의 비용까지 센다)
"""
import argparse
import fnmatch
import json
import os
from datetime import date
from pathlib import Path

from company_graph import agent
from eval.run_agent import ROOT, v2_questions
from eval.v13.run import MODEL, cost, db_ready

HERE = Path(__file__).parent
ARMS = {"P2": {"ASK_SKILLS": "2", "ASK_PAGES": "1"}, "C": {"ASK_SKILLS": "2"}}
PAGES = HERE / "agent" / f"{MODEL}-P2"
STAKE = HERE / "agent" / f"{MODEL}-C"
V6 = {arm: ROOT / "v6" / "agent" / f"{MODEL}-v16{arm}" for arm in ARMS}


def items() -> list[tuple[str, str, str, str, date, Path]]:
    """(방식, 묶음, 번호, 질문, 조회 시점, 저장할 곳)"""
    data = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    when = date.fromisoformat(data["as_of"])
    out = [("P2", group, q["id"], q["question"], when, PAGES / f"{q['id']}.json") for group in ("page", "refuse") for q in data[group]]
    out += [("C", "stake", q["id"], q["question"], when, STAKE / f"{q['id']}.json") for q in data["stake"]]
    for arm in ("P2", "C"):
        out += [(arm, "v6", number, question, as_of, V6[arm] / f"{number}.json") for number, question, _gold, as_of in v2_questions("v6")]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap", type=float, default=0.50)
    parser.add_argument("--only", default="", help="번호를 쉼표로 (예: P31, R*, S*, V*). 비우면 모든 문항")
    args = parser.parse_args()
    todo = items()
    if args.only:
        wanted = args.only.split(",")
        todo = [item for item in todo if any(fnmatch.fnmatchcase(item[2], w) for w in wanted)]
        assert todo, "없는 문항"
    spent = sum(cost(json.loads(path.read_text(encoding="utf-8"))["usage"]) for *_, path in items() if path.exists())
    print(f"문항 {len(todo)}개, 이미 쓴 비용 ${spent:.4f}", flush=True)
    for arm, group, number, question, as_of, path in todo:
        if path.exists():
            continue
        if spent >= args.cap:
            print(f"상한 ${args.cap} 에 닿아 멈춤 (누적 ${spent:.4f})", flush=True)
            return
        if not db_ready():
            print(f"DB에 닿지 않아 멈춤 (누적 ${spent:.4f})", flush=True)
            return
        for key in ("ASK_SKILLS", "ASK_PAGES"):
            os.environ.pop(key, None)
        os.environ.update(ARMS[arm])
        try:
            result = agent.answer(question, model=MODEL, as_of=as_of)
        finally:
            for key in ARMS[arm]:
                os.environ.pop(key, None)
        result["number"], result["group"], result["arm"] = number, group, arm
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        spent += cost(result["usage"])
        tries = [c for c in result["tool_calls"] if c["tool"] == "save_page"]
        removed = (result.get("page_spec") or {}).get("removed_counts", [])
        print(f"{arm} {group} {number} {result['model']} {result['seconds']}초 도구 {len(result['tool_calls'])}회 save_page {len(tries)}번"
              f"(오류 {sum(bool(c['error']) for c in tries)}) 페이지 {'O' if result.get('page_spec') else '-'} 지운 문장 {len(removed)} "
              f"없는 근거 {len(result['cited_not_in_results'])} 이 문항 ${cost(result['usage']):.5f} 누적 ${spent:.4f}", flush=True)
    print(f"끝. 누적 ${spent:.4f}", flush=True)


if __name__ == "__main__":
    main()
