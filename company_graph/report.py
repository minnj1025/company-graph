"""기업 분석 보고서. 질문에 답하는 Agent 와 달리, 무엇을 볼지 정해져 있으므로 정해진 흐름으로 만든다.

1. 모으기: 항목마다 정해진 조회 도구를 코드가 부른다 (모델 없이, 빠뜨리는 항목이 없다)
2. 쓰기: 항목마다 모델이 모은 사실만으로 짧게 쓴다. 항목끼리는 서로 기다리지 않으므로 동시에 쓴다
3. 검산: 글의 숫자를 그 항목의 조회 결과와 대조하고(verify.py), 걸린 항목만 한 번 다시 쓰게 한다
4. 요약: 항목들의 글만 보고 3~5문장. 새 숫자를 만들지 않는다
카드는 항목마다 서버가 DB에서 채운다(views.py). '이 DB로 알 수 없는 것'은 고정된 목록에 빈 항목을 더해 코드가 쓴다.

실행: python -m company_graph.report <회사 이름> [조회 시점]   (마크다운을 찍는다. 유료: 항목마다 모델을 한 번씩 부른다)
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import agent_tools, verify, views
from .config import secret
from .db import session

MODEL = os.environ.get("REPORT_MODEL", "claude-haiku-5-5")
LONG = 14000   # 한 항목에 넘기는 사실의 글자 수 상한
# 항목 쓰기는 모은 사실을 옮기는 일이라 긴 추론이 필요 없다. 켜 두면 Haiku 가 출력 한도를 추론에 다 써서 글이 비거나 잘렸다 (10판 첫 실행)
THINKING = {"type": "disabled"}

RULES = """당신은 한국 상장사의 공시(DART)에서 모은 사실로 기업 분석 보고서의 한 항목을 쓰는 사람입니다.

지킬 것
- 아래에 주어진 조회 결과에 있는 사실만 씁니다. 조회 결과에 없는 수치나 사건을 지어내지 않습니다.
- 사실마다 근거 접수번호를 괄호에 붙입니다. 예: (접수번호 20260814003699)
- 원인을 판단하거나 앞을 전망하지 않습니다. "~때문에", "긍정적", "부정적", "기대된다", "우려된다" 같은 말을 쓰지 않습니다. 매수·매도·목표주가는 말하지 않습니다.
- 결정 공시(증자, 사채, 자기주식, 공급계약, 지분 취득·처분)는 결정이지 실행 결과가 아닙니다. "했다"가 아니라 "결정을 공시했다"로 씁니다.
- 금액은 조회 결과에 적힌 '89조 4,924억 원' 같은 글을 그대로 옮깁니다. 원 단위로 풀어 쓰지 않습니다. 기간(석 달, 누적, 사업연도)과 연결·별도를 밝힙니다.
- 자료가 없으면 "공시 없음" 또는 "이 DB에 없음"이라고 한 문장으로 적고 끝냅니다. 다른 항목에 있는 결정이면 그 항목을 가리킵니다.
- 관계의 방향은 조회 결과의 role 에 적힌 대로 씁니다(누가 누구의 주식을 가졌는지, 누가 팔고 누가 샀는지). 뒤집지 않습니다.
- 건수, 합계, 가장 큰 것은 summary 나 by_kind 에 계산된 값만 씁니다. 목록을 직접 세거나 더하지 않습니다. 서로 다른 종류의 금액(예: 자기주식 직접 취득과 신탁계약)을 더하지 않습니다.
- 지분율과 최대주주는 기준일(어느 보고서, 언제 기준인지)과 함께 씁니다. 요약에서도 그렇습니다.
- 실적의 흐름을 "적자가 이어졌다", "흑자 전환" 같은 말로 묶지 않고 분기 값을 그대로 적습니다. 흑자 전환·적자 전환은 조회 결과의 yoy 칸에 그렇게 적힌 경우에만 씁니다.
- 배당성향은 payout_basis(연결·별도)를 함께 씁니다. 시장 중앙값은 "이 DB가 같은 시장 상장사로 계산한 값"이라고 밝힙니다.
- 마크다운으로 씁니다. 제목은 쓰지 않습니다(화면이 붙입니다). 3~7개의 짧은 글머리표나 두세 문단, 길어도 900자 안쪽입니다. 표는 쓰지 않습니다(카드가 따로 붙습니다).
"""

UNKNOWN = ["실적 전망과 증권사 의견, 목표주가", "사업부문별 실적과 현금흐름표", "업황, 원자재 가격, 수급(기관·외국인), 공매도",
           "결정 공시의 실제 실행 결과(납입, 사채 전환, 자기주식 실제 취득·소각, 계약 이행)", "주가 자체(수익률만 가공해 보여 준다)"]


def _call(db, name: str, **arguments) -> dict:
    return agent_tools.call(db, name, arguments)


def _trim(value, limit: int = LONG) -> str:
    """조회 결과를 글로. 너무 길면 목록을 줄인다 (모델이 쓰는 것은 그 항목의 요점이다)."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    if isinstance(value, dict):
        short = {}
        for key, item in value.items():
            short[key] = item[:8] if isinstance(item, list) and len(item) > 8 else ({k: (v[:8] if isinstance(v, list) else v) for k, v in item.items()} if isinstance(item, dict) else item)
        text = json.dumps(short, ensure_ascii=False, default=str)
    return text[:limit]


def gather(db, company_id: int, as_of: date) -> list[dict]:
    """항목마다 조회 결과(사실)와 붙일 카드 종류. 모델 없이 코드가 부른다."""
    when = as_of.isoformat()
    year_ago = (as_of - timedelta(days=365)).isoformat()
    clean = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}
    from . import report_facts as shape
    holders = clean(_call(db, "get_relations", company_id=company_id, as_of=when, rel_type="equity", direction="in"))
    invested = clean(_call(db, "get_relations", company_id=company_id, as_of=when, rel_type="equity", direction="out", listed_only=False))
    major = clean(_call(db, "get_major_holders", company_id=company_id, as_of=when))
    events = clean(_call(db, "get_capital_events", company_id=company_id, as_of=when))
    recent_events = sum(1 for e in events.get("events", []) if e.get("date", "") >= year_ago)
    sections = [
        {"key": "business", "title": "사업 구성", "cards": ["revenue"],
         "facts": {"products": clean(_call(db, "get_products", company_id=company_id, as_of=when)),
                   "business": clean(_call(db, "get_business", company_id=company_id, as_of=when, sections=[1]))}},
        {"key": "financials", "title": "실적과 재무", "cards": ["financials", "health"],
         "facts": {"financials": shape.financials(clean(_call(db, "get_financials", company_id=company_id, as_of=when)))}},
        {"key": "governance", "title": "지배 구조와 주주", "cards": ["ownership", "holders"],
         "facts": {"governance": shape.governance(company_id, holders, invested, major)}},
        {"key": "deals", "title": "최근 1년의 계약과 결정", "cards": ["supply_flow", "timeline"],
         "facts": {"deals": shape.deals(
             company_id,
             {"relations": full_relations(db, company_id, as_of, "supply_contract", as_of - timedelta(days=365))},
             clean(_call(db, "get_relations", company_id=company_id, as_of=when, rel_type="stake_acquisition", direction="both", disclosed_from=year_ago)),
             clean(_call(db, "get_relations", company_id=company_id, as_of=when, rel_type="stake_disposal", direction="both", disclosed_from=year_ago)),
             recent_events, year_ago)}},
        {"key": "capital", "title": "자본 변동과 주주 환원", "cards": ["dilution", "payout"],
         "facts": {"capital": shape.capital(events, clean(_call(db, "get_dividends", company_id=company_id, as_of=when)))}},
        {"key": "insiders", "title": "내부자 매매", "cards": ["insiders"],
         "facts": {"insider_trades": shape.insiders(clean(_call(db, "get_insider_trades", company_id=company_id, as_of=when)))}},
        {"key": "price", "title": "주가", "cards": ["returns"],
         "facts": {"price_history": clean(_call(db, "get_price_history", company_id=company_id, as_of=when))}},
    ]
    return sections


def full_relations(db, company_id: int, as_of: date, rel_type: str, since: date) -> list[dict]:
    """조회 도구(get_relations)는 50건에서 자른다. 보고서의 건수·합계는 전체로 세야 해서 원장에서 바로 읽는다."""
    from . import query
    rows = [e for e in query.relations(db, as_of, company_ids=[company_id], rel_types=[rel_type]) if e["disclosed_date"] >= since]
    return [agent_tools._edge(db, e) for e in sorted(rows, key=lambda e: e["disclosed_date"], reverse=True)]


def empty(section: dict) -> bool:
    """조회 결과가 모두 비었나 (오류이거나 목록이 비었다)."""
    def has(value) -> bool:
        if not isinstance(value, dict) or "error" in value:
            return False
        for key in ("relations", "rows", "quarters", "holders", "events", "years", "reporters", "periods", "products", "sections",
                    "contracts", "stake_decisions", "investments", "major_holders_5pct", "decisions", "dividends", "buybacks_by_year"):
            if value.get(key):
                return True
        if isinstance(value.get("largest_holder_table"), dict) and value["largest_holder_table"].get("rows"):
            return True
        if isinstance(value.get("summary"), dict) and value["summary"].get("capital_decisions_elsewhere"):
            return True
        return bool(value.get("total"))
    return not any(has(v) for v in section["facts"].values())


def cards(db, section: dict, company_id: int, as_of: date) -> list[dict]:
    out = []
    for kind in section["cards"]:
        try:
            view = views.build(db, agent_tools, kind, company_id, as_of.isoformat())
        except Exception:   # 그 회사에 그 카드의 자료가 없다
            continue
        if view.get("summary") and "없음" not in str(view.get("summary")):
            out.append(view)
    return out


def write(client, company: dict, as_of: date, section: dict, checking: bool) -> dict:
    """한 항목을 쓰고, 검산에 걸리면 한 번 다시 쓰게 한다."""
    facts = _trim(section["facts"])
    messages = [{"role": "user", "content": f"회사: {company['name']} ({company.get('stock_code') or ''}, {company.get('market') or ''})\n"
                                            f"조회 시점: {as_of.isoformat()}\n항목: {section['title']}\n\n조회 결과:\n{facts}"}]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    flagged, retried = [], False
    for attempt in range(2):
        response = client.messages.create(model=MODEL, max_tokens=3000, system=[{"type": "text", "text": RULES, "cache_control": {"type": "ephemeral"}}],
                                          messages=messages, thinking=THINKING)
        for key in usage:
            usage[key] += getattr(response.usage, key, 0) or 0
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not checking:
            break
        flagged = verify.unsupported(text, [facts], as_of, section["title"])
        if not flagged or attempt == 1:
            break
        retried = True
        messages += [{"role": "assistant", "content": text}, {"role": "user", "content": verify.feedback(flagged) + " 이 항목을 처음부터 다시 쓰세요."}]
    return {"key": section["key"], "title": section["title"], "text": text, "flagged": flagged, "retried": retried, "usage": usage}


def summarize(client, company: dict, as_of: date, written: list[dict]) -> tuple[str, dict]:
    body = "\n\n".join(f"## {s['title']}\n{s['text']}" for s in written)
    response = client.messages.create(
        model=MODEL, max_tokens=1500, thinking=THINKING, system=[{"type": "text", "text": RULES, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"회사: {company['name']}\n조회 시점: {as_of.isoformat()}\n항목: 요약\n\n"
                                              f"아래 항목들의 글만 보고 3~5문장으로 요약하세요. 글에 없는 숫자를 만들지 말고, 접수번호는 붙이지 않아도 됩니다.\n\n{body}"}])
    usage = {key: getattr(response.usage, key, 0) or 0 for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    return "".join(b.text for b in response.content if b.type == "text").strip(), usage


def build(company_id: int, as_of: date, *, checking: bool | None = None, client=None, on_section=None) -> dict:
    """보고서 하나. on_section(항목) 은 항목이 하나 끝날 때마다 불린다(화면에 흘려보낼 때)."""
    import anthropic
    # 검산(숫자 대조 뒤 다시 쓰기)은 기본으로 끈다: 10판 1차에서 켠 쪽이 틀린 사실이 있는 보고서가 더 많고(8 대 6),
    # 다시 쓴 항목에 고친다는 메모가 남았다. REPORT_VERIFY=1 이면 켠다
    checking = os.environ.get("REPORT_VERIFY", "0") == "1" if checking is None else checking
    client = client or anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    started = time.time()
    with session() as db:
        company = agent_tools._brief(agent_tools._company(db, company_id))
        sections = gather(db, company_id, as_of)
        for section in sections:
            section["empty"] = empty(section)
            section["views"] = [] if section["empty"] else cards(db, section, company_id, as_of)
    written: dict[str, dict] = {}

    def one(section: dict) -> dict:
        if section["empty"]:
            result = {"key": section["key"], "title": section["title"], "text": "이 회사의 이 항목은 조회 시점까지 공시된 것이 없거나 이 DB가 모으지 않았습니다.",
                      "flagged": [], "retried": False, "usage": {}}
        else:
            result = write(client, company, as_of, section, checking)
        result["views"] = section["views"]
        result["sources"] = sorted(set(verify_receipts(result["text"])))
        if on_section:
            on_section(result)
        return result

    with ThreadPoolExecutor(max_workers=7) as pool:
        for result in pool.map(one, sections):
            written[result["key"]] = result
    ordered = [written[s["key"]] for s in sections]
    summary, summary_usage = summarize(client, company, as_of, ordered)
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    for part in [s["usage"] for s in ordered] + [summary_usage]:
        for key, value in part.items():
            usage[key] += value
    unknown = UNKNOWN + [f"{s['title']}: 조회 시점까지 공시된 것이 없거나 모으지 않음" for s in ordered if not s["views"] and "공시된 것이 없거나" in s["text"]]
    return {"company": company, "as_of": as_of.isoformat(), "model": MODEL, "verified": checking, "summary": summary,
            "sections": ordered, "unknown": unknown, "usage": usage, "seconds": round(time.time() - started, 1)}


def verify_receipts(text: str) -> list[str]:
    import re
    return re.findall(r"\b\d{14}\b", text)


def markdown(report: dict) -> str:
    """내려받기용 마크다운. 카드는 요약 문장으로 옮긴다."""
    lines = [f"# {report['company']['name']} 기업 분석 보고서", "",
             f"조회 시점 {report['as_of']} · 공시(DART)에서 모은 사실만 정리했습니다. 원인 판단, 전망, 투자 권유가 아닙니다.", "",
             "## 요약", "", report["summary"], ""]
    for section in report["sections"]:
        lines += [f"## {section['title']}", "", section["text"], ""]
        for view in section["views"]:
            lines += [f"> 카드: {view['title']} — {view.get('summary') or ''}", ""]
    lines += ["## 이 DB로 알 수 없는 것", ""] + [f"- {item}" for item in report["unknown"]] + [""]
    lines += [f"근거 공시는 https://dart.fss.or.kr/dsaf001/main.do?rcpNo=접수번호 에서 열 수 있습니다. 작성 모델 {report['model']}."]
    return "\n".join(lines)


if __name__ == "__main__":
    name = sys.argv[1]
    when = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    with session() as db:
        found = agent_tools.call(db, "find_company", {"name": name})["candidates"][0]
    result = build(found["company_id"], when)
    sys.stdout.reconfigure(encoding="utf-8")
    print(markdown(result))
    print("\n", result["usage"], result["seconds"], "초", file=sys.stderr)
