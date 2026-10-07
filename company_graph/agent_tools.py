"""Agent가 쓰는 도구. Agent는 SQL을 쓰지 않고 이 함수들만 부른다.

조회 규칙(시점, 최신 보고서, 정정)은 query.py 가 이미 적용한다. 여기서는 Agent가 틀리기 쉬운 곳을 막는다.
  - 조회 시점(as_of)은 모든 도구에서 필수다. 빼먹으면 오류를 돌려준다
  - 기업은 find_company 가 준 company_id 로만 가리킨다. 이름으로 바로 조회하지 못한다
  - 결과는 정해진 순서로 정렬하고, 많으면 자른 뒤 전체 건수와 잘렸다는 사실을 같이 준다
  - 결과가 비었을 때 "공시된 것이 없음"인지 "모으지 않음"인지 가릴 수 있게 수집 범위를 같이 준다
  - 모든 줄에 근거 공시의 접수번호가 있다
"""
from datetime import date

from sqlalchemy import select

from . import query
from .db import Company, Document, Relation

LIMIT = 50
DART_LINK = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
MARKETS = {"Y": "유가증권", "K": "코스닥", "N": "코넥스", "E": "비상장 등"}
ATTRS = ("title", "kind", "period_start", "period_end", "party_hidden", "party_relation", "subsidiary", "ratio_pct",
         "purpose", "method", "pct_after", "nationality", "relation", "ratio_check", "correction_reason", "listed")

_AS_OF = {"type": "string", "description": "조회 시점 (YYYY-MM-DD). 이 날짜까지 공시된 것만 본다. 질문에 시점이 없으면 오늘 날짜"}
_COMPANY = {"type": "integer", "description": "find_company 가 돌려준 company_id"}
TOOLS = [
    {"name": "find_company",
     "description": "이름이나 6자리 종목코드로 기업을 찾는다. 다른 도구를 쓰기 전에 반드시 이것으로 company_id 를 얻는다. "
                    "후보가 여럿이면 종목코드와 시장을 보고 고르고, 고를 수 없으면 사용자에게 되묻는다.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "get_relations",
     "description": "한 기업의 관계를 조회한다. rel_type: equity(지분, 사업보고서 기준), affiliate(계열회사), "
                    "supply_contract(단일판매·공급계약 공시), stake_acquisition / stake_disposal(타법인 주식 취득·처분 결정). "
                    "direction: out 은 이 기업이 주체(지분을 가진 쪽, 판 쪽, 결정한 쪽), in 은 이 기업이 상대(지분을 내준 쪽, 산 쪽, 대상). "
                    "지분과 계열은 그 시점에 나와 있는 가장 최근 사업보고서의 값이다. 공급계약과 취득·처분 결정은 그 시점에 유효한 공시이고 "
                    "정정 전 값이나 철회된 결정은 빠진다. 공시일 범위를 좁히려면 disclosed_from, disclosed_to 를 쓴다. "
                    "\"그 기간에 나온 공시를 모두\"처럼 공시 건수를 세는 질문에는 include_superseded=true 로, 나중에 정정된 공시까지 받는다. "
                    "결과가 잘리면(truncated) counterparty_id 나 공시일 범위로 좁혀 다시 부른다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "rel_type": {"type": "string", "enum": ["equity", "affiliate", "supply_contract", "stake_acquisition", "stake_disposal"]},
         "direction": {"type": "string", "enum": ["out", "in", "both"]},
         "disclosed_from": {"type": "string", "description": "이 날짜부터 공시된 것만 (YYYY-MM-DD)"},
         "disclosed_to": {"type": "string", "description": "이 날짜까지 공시된 것만 (YYYY-MM-DD)"},
         "listed_only": {"type": "boolean", "description": "상대가 상장사인 줄만"},
         "counterparty_id": {"type": "integer", "description": "관계의 반대쪽이 이 기업인 줄만 (find_company 의 company_id)"},
         "include_superseded": {"type": "boolean", "description": "나중에 정정·해지·철회로 무효가 된 공시도 포함 "
                                                                  "(공급계약, 취득·처분 결정에만 뜻이 있다)"}},
         "required": ["company_id", "as_of", "rel_type", "direction"]}},
    {"name": "get_filings",
     "description": "한 기업이 낸 공시 목록(공급계약, 취득·처분 결정, 사업보고서)을 접수 순으로 준다. 정정·해지·철회 공시가 포함되고, "
                    "어느 공시를 고친 것인지(corrects)와 그 시점에 최신본인지가 나온다. 계약이 해지됐는지, 결정이 철회됐는지, "
                    "정정 전에는 값이 무엇이었는지 확인할 때 쓴다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "doc_type": {"type": "string", "enum": ["supply_contract", "event", "annual"]},
         "filed_from": {"type": "string"}, "filed_to": {"type": "string"}},
         "required": ["company_id", "as_of", "doc_type"]}},
    {"name": "find_paths",
     "description": "두 기업을 잇는 가장 짧은 관계 경로를 찾는다 (최대 3단계, 방향 무시).",
     "input_schema": {"type": "object", "properties": {"from_company_id": _COMPANY, "to_company_id": _COMPANY, "as_of": _AS_OF},
                      "required": ["from_company_id", "to_company_id", "as_of"]}},
    {"name": "get_coverage",
     "description": "이 DB가 무엇을 언제부터 언제까지 모았고 무엇을 모으지 않았는지. 결과가 비었을 때 답하기 전에 확인한다.",
     "input_schema": {"type": "object", "properties": {}}},
]


class ToolError(ValueError):
    """Agent에게 그대로 돌려줄 오류. 무엇을 고쳐 다시 부르면 되는지 적는다."""


def _date(value, name: str, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise ToolError(f"{name} 이 없습니다. YYYY-MM-DD 로 주세요")
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolError(f"{name}={value!r} 를 날짜로 읽을 수 없습니다. YYYY-MM-DD 로 주세요") from None


def _company(db, company_id) -> Company:
    company = db.get(Company, company_id) if isinstance(company_id, int) else None
    if company is None:
        raise ToolError(f"company_id={company_id!r} 인 기업이 없습니다. find_company 로 먼저 찾으세요")
    return company


def _brief(company: Company | None) -> dict | None:
    if company is None:
        return None
    return {"company_id": company.company_id, "name": company.name, "stock_code": company.stock_code,
            "market": MARKETS.get(company.corp_cls, "원장에 이름만 있음"), "group": company.ftc_group}


def find_company(db, name: str) -> dict:
    found = query.find_companies(db, name.strip())
    return {"candidates": [_brief(c) for c in found],
            "note": None if found else "원장에 없는 이름입니다. 상장사와 그 계열회사만 들어 있습니다"}


def _edge(db, edge: dict) -> dict:
    subject, obj = db.get(Company, edge["subject_id"]), db.get(Company, edge["object_id"]) if edge["object_id"] else None
    value = edge["value"]
    return {"type": query.LABELS[edge["type"]],
            "subject": _brief(subject),
            "object": _brief(obj) or {"company_id": None, "name": edge["object_name_raw"], "note": "원장에 없는 상대 (이름만 있음)"},
            "value": None if value is None else (float(value) if edge["unit"] == "pct" else int(value)),
            "unit": {"pct": "%", "krw": "원"}.get(edge["unit"]),
            "as_of_date": edge["as_of_date"] and edge["as_of_date"].isoformat(),
            "disclosed_date": edge["disclosed_date"].isoformat(),
            "disclosed_by": {"subject": "주체가 공시", "object": "상대가 공시", "both": "양쪽 공시에서 확인"}[edge["disclosed_by"]],
            "stale": edge["stale"] or None,
            "superseded_on": edge["invalidated_date"] and edge["invalidated_date"].isoformat(),
            "detail": {k: edge["attrs"][k] for k in ATTRS if edge["attrs"].get(k) not in (None, "", False)},
            "rcept_no": edge["evidence"]}


def get_relations(db, company_id, as_of, rel_type: str, direction: str, disclosed_from=None, disclosed_to=None,
                  listed_only: bool = False, counterparty_id=None, include_superseded: bool = False) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    if rel_type not in query.LABELS:
        raise ToolError(f"rel_type={rel_type!r} 은 없습니다")
    if direction not in ("out", "in", "both"):
        raise ToolError("direction 은 out, in, both 중 하나입니다")
    start, end = _date(disclosed_from, "disclosed_from"), _date(disclosed_to, "disclosed_to")
    if rel_type == "affiliate" and direction == "out":
        edges = query.group_members(db, company.company_id, when)
    else:
        edges = query.relations(db, when, company_ids=[company.company_id], rel_types=[rel_type], direction=direction,
                                include_superseded=bool(include_superseded) and rel_type not in ("equity", "affiliate"))
    if counterparty_id is not None:
        other = _company(db, counterparty_id).company_id
        edges = [e for e in edges if other in (e["subject_id"], e["object_id"])]
    edges = [e for e in edges if (start is None or e["disclosed_date"] >= start) and (end is None or e["disclosed_date"] <= end)]
    rows = [_edge(db, e) for e in edges]
    if listed_only:
        other = "object" if direction != "in" else "subject"
        rows = [r for r in rows if r[other].get("market") in ("유가증권", "코스닥", "코넥스")]
    rows.sort(key=lambda r: (r["disclosed_date"], r["rcept_no"][0], r["object"]["name"]))
    sources = query.coverage(db)
    return {"company": _brief(company), "as_of": when.isoformat(), "total": len(rows), "truncated": len(rows) > LIMIT,
            "relations": rows[:LIMIT],
            "coverage": {"source": sources["sources"].get(rel_type), **{k: str(v) for k, v in
                         sources["relations"].get(rel_type, {}).items() if k in ("first_disclosed", "last_disclosed")}},
            "note": query.NOTICE if not rows else (
                "이 목록에 조회한 기업 자신은 들어 있지 않습니다. 같은 집단의 회사를 세거나 나열할 때는 자신을 더하세요"
                if rel_type == "affiliate" and direction == "out" else None)}


def get_filings(db, company_id, as_of, doc_type: str, filed_from=None, filed_to=None) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    start, end = _date(filed_from, "filed_from"), _date(filed_to, "filed_to")
    docs = db.scalars(select(Document).where(Document.company_id == company.company_id, Document.doc_type == doc_type,
                                             Document.rcept_dt <= when).order_by(Document.rcept_no)).all()
    superseded = {d.corrects_rcept_no for d in docs if d.corrects_rcept_no}
    rows = []
    for doc in docs:
        if (start and doc.rcept_dt < start) or (end and doc.rcept_dt > end):
            continue
        relations = db.scalars(select(Relation).where(Relation.rcept_no == doc.rcept_no, Relation.retired_at.is_(None))).all()
        rows.append({"rcept_no": doc.rcept_no, "filed": doc.rcept_dt.isoformat(), "report": doc.report_nm,
                     "is_correction": bool(doc.is_correction), "corrects": doc.corrects_rcept_no,
                     "superseded_by_later_filing": doc.rcept_no in superseded,
                     "contents": [{"counterparty": r.object_name_raw, "value": None if r.value_num is None else float(r.value_num),
                                   "unit": r.value_unit, **{k: (r.attrs or {})[k] for k in ("title", "period_end", "purpose")
                                                            if (r.attrs or {}).get(k)}}
                                  for r in relations if r.rel_type not in ("equity", "affiliate")][:5],
                     "url": DART_LINK + doc.rcept_no})
    return {"company": _brief(company), "as_of": when.isoformat(), "total": len(rows), "truncated": len(rows) > LIMIT,
            "filings": rows[-LIMIT:],
            "note": "공급계약과 취득·처분 결정은 2024-01 이후 공시만, 사업보고서는 2023~2025 사업연도만 있습니다"}


def find_paths(db, from_company_id, to_company_id, as_of) -> dict:
    start, goal, when = _company(db, from_company_id), _company(db, to_company_id), _date(as_of, "as_of", required=True)
    found = query.paths(db, start.company_id, goal.company_id, when)
    return {"from": _brief(start), "to": _brief(goal), "as_of": when.isoformat(), "total": len(found),
            "paths": [[_edge(db, e) for e in path] for path in found[:10]],
            "note": None if found else "3단계 안에서 잇는 공시된 관계가 없습니다"}


def get_coverage(db) -> dict:
    covered = query.coverage(db)
    return {"notice": covered["notice"], "sources": covered["sources"],
            "relations": {v["label"]: {"first_disclosed": str(v["first_disclosed"]), "last_disclosed": str(v["last_disclosed"]),
                                       "rows": v["rows"]} for v in covered["relations"].values()},
            "not_collected": ["개인 주주", "반기·분기보고서", "주요 고객(사업보고서 본문 서술)", "뉴스", "주가",
                              "2024-01 이전의 공급계약과 취득·처분 결정"]}


FUNCTIONS = {"find_company": find_company, "get_relations": get_relations, "get_filings": get_filings,
             "find_paths": find_paths, "get_coverage": get_coverage}


def call(db, name: str, arguments: dict) -> dict:
    """도구 하나를 부른다. Agent가 고칠 수 있는 잘못은 예외 대신 error 로 돌려준다."""
    if name not in FUNCTIONS:
        return {"error": f"{name} 이라는 도구는 없습니다"}
    try:
        return FUNCTIONS[name](db, **arguments)
    except ToolError as error:
        return {"error": str(error)}
    except TypeError as error:
        return {"error": f"인자가 맞지 않습니다: {error}"}
