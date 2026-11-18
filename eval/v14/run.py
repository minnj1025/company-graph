"""평가 14판: Agent가 직접 짜는 분석 페이지(ASK_PAGES=1, save_page). Claude API를 부른다(유료, claude-haiku-5-5).

실행: python -m eval.v14.run [--cap 0.35] [--only P01]
- 페이지 요청 15문항, 페이지로 만들면 안 되는 요청 2문항(questions.json), 그리고 6판 카드 20문항(페이지를 만들지 말아야 하는 보통 질문)
- 방식은 하나(P): 지금 기본(ASK_SKILLS=2, 금지 표현 검사 켬)에 ASK_PAGES=1 을 더한다
- 서버를 거치지 않으므로 페이지는 저장되지 않는다. 결과의 page_spec 이 Agent가 낸 설계도다
- 결과: eval/v14/agent/claude-haiku-5-5-P/, eval/v6/agent/claude-haiku-5-5-v14P/. 이미 있는 답은 건너뛴다
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
FLAGS = {"ASK_SKILLS": "2", "ASK_PAGES": "1"}


def items() -> list[tuple[str, str, str, date, Path]]:
    """(묶음, 번호, 질문, 조회 시점, 저장할 곳)"""
    data = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    when = date.fromisoformat(data["as_of"])
    out = [(group, q["id"], q["question"], when, HERE / "agent" / f"{MODEL}-P" / f"{q['id']}.json") for group in ("page", "refuse") for q in data[group]]
    out += [("v6", number, question, as_of, ROOT / "v6" / "agent" / f"{MODEL}-v14P" / f"{number}.json") for number, question, _gold, as_of in v2_questions("v6")]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap", type=float, default=0.35)
    parser.add_argument("--only", default="", help="번호를 쉼표로 (예: P01, R*, V*). 비우면 모든 문항")
    args = parser.parse_args()
    todo = items()
    if args.only:
        wanted = args.only.split(",")
        todo = [item for item in todo if any(fnmatch.fnmatchcase(item[1], w) for w in wanted)]
        assert todo, "없는 문항"
    spent = sum(cost(json.loads(path.read_text(encoding="utf-8"))["usage"]) for *_, path in items() if path.exists())
    print(f"문항 {len(todo)}개, 이미 쓴 비용 ${spent:.4f}", flush=True)
    for group, number, question, as_of, path in todo:
        if path.exists():
            continue
        if spent >= args.cap:
            print(f"상한 ${args.cap} 에 닿아 멈춤 (누적 ${spent:.4f})", flush=True)
            return
        if not db_ready():
            print(f"DB에 닿지 않아 멈춤 (누적 ${spent:.4f})", flush=True)
            return
        os.environ.update(FLAGS)
        try:
            result = agent.answer(question, model=MODEL, as_of=as_of)
        finally:
            for key in FLAGS:
                os.environ.pop(key, None)
        result["number"], result["group"] = number, group
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        spent += cost(result["usage"])
        tries = [c for c in result["tool_calls"] if c["tool"] == "save_page"]
        print(f"{group} {number} {result['seconds']}초 도구 {len(result['tool_calls'])}회 save_page {len(tries)}번"
              f"(오류 {sum(bool(c['error']) for c in tries)}) 페이지 {'O' if result.get('page_spec') else '-'} "
              f"없는 근거 {len(result['cited_not_in_results'])} 누적 ${spent:.4f}", flush=True)
    print(f"끝. 누적 ${spent:.4f}", flush=True)


if __name__ == "__main__":
    main()
