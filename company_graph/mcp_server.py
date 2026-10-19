"""Corpath 의 조회 도구를 MCP 로 내놓는다. 다른 사람의 Claude(또는 사내 Agent)가 자기 모델과 자기 비용으로 이 DB를 조회한다.

우리 쪽에서는 모델을 부르지 않는다. 도구는 DB를 조회해 결과를 돌려줄 뿐이다.
- 주가 도구(get_price_moves, get_price_history)는 내놓지 않는다. 한국거래소 통계정보는 제3자에게 데이터 자체를 제공하지 않는 조건이다
- 화면 도구(show_on_graph)는 움직일 화면이 없으므로 빼고, 대신 결과에 "그래프로 보기" 주소를 붙인다
- 사이트의 Agent 지시문은 따라가지 않으므로 핵심 규칙을 서버 안내문(INSTRUCTIONS)과 도구 설명으로 옮긴다
- 열쇠: 환경 변수 MCP_KEYS(쉼표로 여럿)를 두면 주소의 ?key= 가 그중 하나여야 한다. 비워 두면 열어 둔다

서버리스(Vercel)에서는 요청마다 세션 관리자를 새로 만들어 상태 없이 처리한다(stateless, JSON 응답).
"""
import inspect
import json
import os
from typing import Annotated, Any
from urllib.parse import parse_qs

from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import Field

from . import agent_tools
from .db import session

SITE = os.environ.get("SITE_URL", "https://company-graph-nu.vercel.app")
HIDDEN = {agent_tools.SCREEN_TOOL, "get_price_moves", "get_price_history", *agent_tools.COMPOSITE_TOOLS}
LONG = 30000   # 결과가 이보다 길면 목록을 줄인다 (쓰는 쪽의 토큰을 아끼려고)

INSTRUCTIONS = """Corpath: 한국 상장사의 공시(DART)에서 뽑은 기업 관계 DB. 지분, 계열, 공급계약, 지분 취득·처분 결정, 제품과 매출 비중, 사업보고서의 사업 내용을 조회한다.

이 도구로 답할 때 지킬 것
- 먼저 find_company 로 company_id 를 얻는다. 조회 도구는 as_of(조회 시점, YYYY-MM-DD)를 받고, 그날까지 공시된 것만 돌려준다. 질문에 시점이 없으면 오늘 날짜를 쓴다.
- 답에 적는 값마다 결과의 rcept_no(접수번호)를 근거로 붙인다. 결과에 없는 값은 지어내지 않는다.
- 결과가 비었으면 get_coverage 로 수집 범위를 보고 "공시된 것이 없다"와 "이 DB가 모으지 않았다"를 구분한다.
- 주주 목록은 최대주주와 특수관계인, 그리고 지분을 자기 보고서에 적은 회사만 있다. 5% 이상 주주 전체가 아니다. detail.share_class 가 있는 줄은 보통주가 아니고, treasury 는 자기주식, exited 는 기간 중에 0이 된 줄이다.
- 원인이나 평가를 묻는 질문("왜 주가가 못 오르나", "전망은")에는 원인을 판단하거나 전망하지 않는다. 그 질문을 따져 볼 사실(사업 구성, 계약, 지분 변화)을 근거와 함께 모으고, 이 DB에 없는 것(실적 추이, 업황, 주가, 증권사 의견)을 밝힌다.
- 매수·매도·보유 판단은 하지 않는다.
- 결과의 graph_link 는 그 기업들을 Corpath 사이트의 관계 그래프로 여는 주소다. 사용자에게 함께 알려 주면 좋다.
"""

server = MCPServer(name="Corpath", title="Corpath — 공시 기반 기업 관계", instructions=INSTRUCTIONS,
                   website_url=SITE, version="1")

_TYPES = {"string": str, "integer": int, "boolean": bool, "number": float, "array": list, "object": dict}


def _ids(value: Any, found: list[int]) -> None:
    """결과 안의 company_id 를 나온 순서대로 모은다 (그래프 링크용)."""
    if isinstance(value, dict):
        if isinstance(value.get("company_id"), int) and value["company_id"] not in found:
            found.append(value["company_id"])
        for item in value.values():
            _ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _ids(item, found)


def _shrink(result: dict) -> dict:
    """너무 긴 목록은 앞부분만 남기고 줄였다고 적는다."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= LONG:
        return result
    out = dict(result)
    for key, value in result.items():
        if isinstance(value, list) and len(value) > 10:
            out[key] = value[:10]
            out[f"{key}_shown"] = f"{len(value)}개 가운데 앞 10개만 보였습니다. 조건을 좁혀 다시 부르세요"
    return out


def _register(spec: dict) -> None:
    name, schema = spec["name"], spec["input_schema"]
    required = set(schema.get("required", []))
    params = []
    for key, prop in schema.get("properties", {}).items():
        kind = _TYPES.get(prop.get("type"), str)
        described = Annotated[kind if key in required else kind | None, Field(description=prop.get("description", ""))]
        params.append(inspect.Parameter(key, inspect.Parameter.KEYWORD_ONLY, annotation=described,
                                        default=inspect.Parameter.empty if key in required else None))

    def run(**arguments) -> str:
        arguments = {k: v for k, v in arguments.items() if v is not None}
        with session() as db:
            result = agent_tools.call(db, name, arguments)
        result = {k: v for k, v in result.items() if not k.startswith("_")}
        found: list[int] = []
        _ids(result, found)
        if found and "error" not in result:
            result["graph_link"] = f"{SITE}/?show={','.join(map(str, found[:20]))}"
        return json.dumps(_shrink(result), ensure_ascii=False, default=str)

    run.__signature__ = inspect.Signature(params, return_annotation=str)
    run.__name__ = name
    server.add_tool(run, name=name, description=spec["description"])


for _spec in agent_tools.TOOLS:
    if _spec["name"] not in HIDDEN:
        _register(_spec)


def _allowed(scope) -> bool:
    keys = {k.strip() for k in os.environ.get("MCP_KEYS", "").split(",") if k.strip()}
    if not keys:
        return True
    given = parse_qs(scope.get("query_string", b"").decode()).get("key", [""])[0]
    return given in keys


class App:
    """FastAPI 에 /api/mcp 로 붙이는 ASGI 앱 (Route 가 함수가 아닌 객체를 ASGI 앱으로 다루므로 클래스로 둔다)."""

    async def __call__(self, scope, receive, send):
        await handle(scope, receive, send)


async def handle(scope, receive, send):
    """ 요청마다 상태 없는 세션 관리자를 새로 띄운다 (서버리스에서는 앱의 수명 주기를 믿을 수 없어서)."""
    if scope["type"] != "http":
        return
    if not _allowed(scope):
        body = b'{"error": "key"}'
        await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
        return
    manager = StreamableHTTPSessionManager(app=server._lowlevel_server, json_response=True, stateless=True)
    async with manager.run():
        await manager.handle_request(scope, receive, send)


def tool_names() -> list[str]:
    return [s["name"] for s in agent_tools.TOOLS if s["name"] not in HIDDEN]

