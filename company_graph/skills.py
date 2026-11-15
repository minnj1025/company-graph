"""질문 유형별 규칙(skill). 지시문에는 이름과 한 줄 설명만 두고, Agent가 read_skill 로 필요한 것만 펼쳐 읽는다.

skill 하나는 skills/<이름>/SKILL.md 파일 하나다. 맨 위의 name, description 다음이 규칙 본문이다.
본문은 agent.SYSTEM, agent.WHY 의 절과 글자까지 같다(tests/test_skills.py). 두 방식의 차이가 규칙의 위치뿐이게 하려는 것이다.
환경 변수 ASK_SKILLS=1 일 때만 쓴다.
"""
import os
from functools import cache
from pathlib import Path

DIR = Path(__file__).with_name("skills")
TOOL = "read_skill"


def enabled() -> bool:
    return os.environ.get("ASK_SKILLS", "0") == "1"


@cache
def load() -> dict[str, dict]:
    """{이름: {"description": 설명, "body": 규칙 본문}}. 이름 순서는 폴더 이름 순."""
    out = {}
    for path in sorted(DIR.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")   # Windows 에서 줄바꿈이 바뀌어도 같게
        _, head, body = text.split("---\n", 2)
        meta = dict(line.split(": ", 1) for line in head.strip().splitlines())
        assert meta["name"] == path.parent.name, path
        out[meta["name"]] = {"description": meta["description"], "body": body.strip()}
    return out


def names(*, why: bool = True) -> list[str]:
    return [n for n in load() if why or n != "why-questions"]


def catalog(*, why: bool = True) -> str:
    """지시문에 넣는 목록. 규칙 본문은 넣지 않는다."""
    lines = [
        "질문 유형별 규칙 (skill)",
        f"- 아래 유형의 질문에는 따로 지킬 규칙이 있습니다. 질문이 어느 유형에 들면, 첫 차례에 find_company 같은 조회와 함께 {TOOL} 로 그 규칙을 불러 읽고 따릅니다. "
        "여러 유형에 걸치면 함께 부릅니다. 어느 유형에도 들지 않으면 부르지 않습니다.",
        "- 앞선 질문에서 읽은 규칙은 이번 질문에 남아 있지 않습니다. 이어지는 질문이라도 이번 질문에 맞는 규칙을 다시 부릅니다.",
    ]
    lines += [f"- {n}: {load()[n]['description']}" for n in names(why=why)]
    return "\n".join(lines)


def tool(*, why: bool = True) -> dict:
    return {
        "name": TOOL,
        "description": "질문 유형별 규칙(skill)을 읽습니다. 지시문의 '질문 유형별 규칙' 목록에 있는 이름을 넘깁니다. "
                       "조회 도구와 같은 차례에 함께 부를 수 있습니다. 공시 사실은 돌려주지 않습니다.",
        "input_schema": {
            "type": "object",
            "properties": {"names": {"type": "array", "items": {"type": "string", "enum": names(why=why)}, "minItems": 1}},
            "required": ["names"],
        },
    }


def read(arguments: dict, *, why: bool = True, rewrite=None) -> dict:
    """rewrite(이름, 본문): 말투에 따라 본문을 바꿀 때 쓴다(agent.WHY_STYLE)."""
    asked = arguments.get("names") or []
    if isinstance(asked, str):
        asked = [asked]
    known = names(why=why)
    wrong = [n for n in asked if n not in known]
    if wrong or not asked:
        return {"error": f"없는 규칙: {', '.join(wrong) or '(이름 없음)'}. 쓸 수 있는 이름: {', '.join(known)}"}
    return {"skills": [{"name": n, "rules": rewrite(n, load()[n]["body"]) if rewrite else load()[n]["body"]} for n in dict.fromkeys(asked)]}
