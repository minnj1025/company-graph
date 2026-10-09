"""Agent가 쓰는 도구. Agent는 SQL을 쓰지 않고 이 함수들만 부른다.

조회 규칙(시점, 최신 보고서, 정정)은 query.py 가 이미 적용한다. 여기서는 Agent가 틀리기 쉬운 곳을 막는다.
  - 조회 시점(as_of)은 모든 도구에서 필수다. 빼먹으면 오류를 돌려준다
  - 기업은 find_company 가 준 company_id 로만 가리킨다. 이름으로 바로 조회하지 못한다
  - 결과는 정해진 순서로 정렬하고, 많으면 자른 뒤 전체 건수와 잘렸다는 사실을 같이 준다
  - 결과가 비었을 때 "공시된 것이 없음"인지 "모으지 않음"인지 가릴 수 있게 수집 범위를 같이 준다
  - 모든 줄에 근거 공시의 접수번호가 있다
"""
import re
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from . import query
from .db import BusinessSection, Company, Document, Product, Relation

LIMIT = 50
LISTED = ("Y", "K", "N")
DART_LINK = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
MARKETS = {"Y": "유가증권", "K": "코스닥", "N": "코넥스", "E": "비상장 등"}
ATTRS = ("title", "kind", "period_start", "period_end", "party_hidden", "party_relation", "subsidiary", "ratio_pct",
         "recent_sales", "region", "purpose", "method", "pct_after", "shares", "shares_after", "equity_ratio_pct",
         "expected_date", "business", "nationality", "relation", "ratio_check", "correction_reason", "changes",
         "amount_from", "reason", "listed", "relation_to_filer")
# detail 에 나오는 이름의 뜻. 도구 결과에 같이 실어 Agent가 값을 헷갈리지 않게 한다
FIELDS = ("as_of_date: 계약일·결의일·해지일·보고서 기준일. period_start~period_end: 계약기간(시작일~종료일). "
          "ratio_pct: 최근 매출액 대비(%). equity_ratio_pct: 자기자본 대비(%). expected_date: 취득·처분 예정일자. "
          "shares: 취득·처분 주식수, shares_after·pct_after: 거래 뒤 소유주식수·지분율. "
          "changes: 정정 공시의 [항목, 정정 전, 정정 후]. reason: 해지·철회 사유. correction_reason: 정정 사유")

_AS_OF = {"type": "string", "description": "조회 시점 (YYYY-MM-DD). 이 날짜까지 공시된 것만 본다. 질문에 시점이 없으면 오늘 날짜"}
_COMPANY = {"type": "integer", "description": "find_company 가 돌려준 company_id"}
TOOLS = [
    {"name": "find_company",
     "description": "이름이나 6자리 종목코드로 기업을 찾는다. 다른 도구를 쓰기 전에 반드시 이것으로 company_id 를 얻는다. "
                    "후보가 여럿이면 종목코드와 시장을 보고 고르고, 고를 수 없으면 사용자에게 되묻는다.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "get_relations",
     "description": "한 기업의 관계를 조회한다. rel_type: equity(지분, 사업보고서 기준), affiliate(계열회사), "
                    "supply_contract(단일판매·공급계약 공시), supply_termination(공급계약 해지 공시), "
                    "stake_acquisition / stake_disposal(타법인 주식 취득·처분 결정). "
                    "detail.subsidiary 가 있으면 공시는 이 기업(모회사)이 냈지만 실제 계약·결정의 당사자는 그 자회사다. "
                    "direction: out 은 이 기업이 주체(지분을 가진 쪽, 판 쪽, 결정한 쪽), in 은 이 기업이 상대(지분을 내준 쪽, 산 쪽, 대상). "
                    "equity 의 in 은 이 기업의 주주 목록이다: 최대주주와 그 특수관계인(개인 포함, detail.relation_to_filer 에 본인·친인척·계열회사 등), "
                    "그리고 이 기업 지분을 가졌다고 자기 보고서에 적은 다른 회사. 최대주주 본인은 relation_to_filer 로 가린다. "
                    "지분과 계열은 그 시점에 나와 있는 가장 최근 사업보고서의 값이다. 공급계약과 취득·처분 결정은 그 시점에 유효한 공시이고 "
                    "정정 전 값이나 철회된 결정은 빠진다. 공시일 범위를 좁히려면 disclosed_from, disclosed_to 를 쓴다. "
                    "\"그 기간에 나온 공시를 모두\"처럼 공시 건수를 세는 질문에는 include_superseded=true 로, 나중에 정정된 공시까지 받는다. "
                    "결과가 잘리면(truncated) counterparty_id 나 공시일 범위로 좁혀 다시 부른다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "rel_type": {"type": "string", "enum": ["equity", "affiliate", "supply_contract", "supply_termination", "stake_acquisition", "stake_disposal"]},
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
                    "어느 공시를 고친 것인지(corrects)와 그 시점에 최신본인지가 나온다. 해지·철회 공시는 contents 에 무엇을 해지·철회했는지 "
                    "(계약명, 상대, 금액, 사유, 철회 전 값)가 들어 있고, 원래 공시가 수집 기간(2024-01) 이전이어도 이것으로 답할 수 있다. "
                    "계약이 해지됐는지, 결정이 철회됐는지, "
                    "정정 전에는 값이 무엇이었는지 확인할 때 쓴다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "doc_type": {"type": "string", "enum": ["supply_contract", "event", "annual"]},
         "filed_from": {"type": "string"}, "filed_to": {"type": "string"}},
         "required": ["company_id", "as_of", "doc_type"]}},
    {"name": "list_companies",
     "description": "조건에 맞는 기업 목록. industry 는 한국표준산업분류(KSIC) 코드의 앞자리다 (예: 30 자동차 및 트레일러, 303 자동차 부품, "
                    "26 전자부품·컴퓨터·통신장비, 21 의약품, 64 금융). group 은 공정위 대규모기업집단 이름(find_company 결과의 group 값 그대로). "
                    "업종 코드는 DART에 등록된 회사에만 있다.",
     "input_schema": {"type": "object", "properties": {
         "industry": {"type": "string"}, "group": {"type": "string"},
         "listed_only": {"type": "boolean", "description": "지금 상장된 회사만 (기본 true)"}}}},
    {"name": "find_disclosers",
     "description": "기간 안에 어떤 종류의 공시를 낸 기업을 한 번에 찾는다 (기업마다 get_relations 를 부르지 않아도 된다). "
                    "공시를 낸 쪽을 업종(industry)이나 기업집단(group)으로, 상대 쪽을 counterparty_id 나 counterparty_group 으로 좁힐 수 있다. "
                    "기업마다 새로 낸 공시 수(new)와 정정 공시 수(corrections), 접수번호를 준다. 나중에 정정된 공시도 센다.",
     "input_schema": {"type": "object", "properties": {
         "as_of": _AS_OF,
         "rel_type": {"type": "string", "enum": ["supply_contract", "supply_termination", "stake_acquisition", "stake_disposal"]},
         "disclosed_from": {"type": "string"}, "disclosed_to": {"type": "string"},
         "industry": {"type": "string", "description": "공시를 낸 기업의 KSIC 코드 앞자리"},
         "group": {"type": "string", "description": "공시를 낸 기업의 기업집단"},
         "counterparty_id": {"type": "integer", "description": "상대 기업의 company_id"},
         "counterparty_group": {"type": "string", "description": "상대 기업의 기업집단"},
         "listed_only": {"type": "boolean", "description": "공시를 낸 기업이 지금 상장된 회사인 것만 (기본 true)"}},
         "required": ["as_of", "rel_type", "disclosed_from", "disclosed_to"]}},
    {"name": "find_paths",
     "description": "두 기업을 잇는 가장 짧은 관계 경로를 찾는다 (최대 3단계, 방향 무시).",
     "input_schema": {"type": "object", "properties": {"from_company_id": _COMPANY, "to_company_id": _COMPANY, "as_of": _AS_OF},
                      "required": ["from_company_id", "to_company_id", "as_of"]}},
    {"name": "search_business",
     "description": "정기보고서(사업보고서·반기보고서 가운데 조회 시점까지 나온 가장 나중 것)의 '사업의 내용'(회사가 스스로 적은 사업 설명, 제품과 매출 비중 표, 원재료, 매출처)을 낱말로 검색해 "
                    "그 사업을 하는 회사를 찾는다. 업종 분류가 아니라 보고서의 글에서 찾으므로, 이슈나 주제와 관련된 회사를 찾을 때 쓴다. "
                    "keywords 에는 이슈 이름이 아니라 그 이슈가 닿는 제품·서비스·원재료의 이름을 여러 개 넣는다 "
                    "(예: 노벨문학상 → [\"단행본\", \"도서\", \"서점\", \"전자책\", \"인쇄용지\"]). 낱말 하나는 2~15자, 붙여 쓴 그대로 찾는다. "
                    "결과의 snippets 는 보고서에 적힌 문장 그대로이고, 낱말이 스쳐 지나간 회사도 섞여 있으니 "
                    "주력인지 일부인지는 get_business 로 매출 비중 표를 읽고 판단한다.",
     "input_schema": {"type": "object", "properties": {
         "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8,
                      "description": "찾을 낱말들. 여러 낱말에 걸리고 사업 개요나 제품 표에 나온 회사가 앞에 온다"},
         "as_of": _AS_OF,
         "listed_only": {"type": "boolean", "description": "상장사만 (기본 true)"}},
         "required": ["keywords", "as_of"]}},
    {"name": "get_business",
     "description": "한 회사의 가장 나중 정기보고서(사업보고서·반기보고서)에서 '사업의 내용'을 읽는다. 기본은 사업의 개요와 주요 제품 및 서비스(매출 비중 표). "
                    "표는 한 줄이 표의 한 줄이고 칸은 ' | ' 로 나뉜다. 매출 비중은 표에 적힌 숫자 그대로 옮긴다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "sections": {"type": "array", "items": {"type": "integer"},
                      "description": "읽을 소제목 번호. 1 사업의 개요, 2 주요 제품 및 서비스, 3 원재료 및 생산설비, 4 매출 및 수주상황, "
                                     "6 주요계약 및 연구개발활동, 7 기타 참고사항. 기본 [1, 2]"}},
         "required": ["company_id", "as_of"]}},
    {"name": "find_by_product",
     "description": "제품 이름으로 그것을 파는 회사를 찾는다. 정기보고서의 '주요 제품 및 서비스' 매출 비중 표에서 읽은 줄을 찾으므로 "
                    "회사마다 그 제품이 매출에서 차지하는 비중(share_pct)이 바로 나온다. 비중이 큰 회사가 앞에 온다. "
                    "keywords 에는 제품 이름을 여러 표기로 넣는다 (예: [\"분리막\", \"LiBS\"], [\"인쇄회로기판\", \"PCB\"]). "
                    "줄마다 name 과 segment 는 보고서 표에 적힌 그대로이고, std_names 는 여러 회사의 같은 제품을 묶으려고 모델이 붙인 표준 이름이라 "
                    "틀릴 수 있다. 답에는 표에 적힌 이름과 비중을 옮긴다. "
                    "표를 읽지 못한 회사와 제품 표가 없는 회사(금융업 등)는 여기에 나오지 않으므로, 빠짐없이 찾아야 하면 search_business 도 함께 쓴다.",
     "input_schema": {"type": "object", "properties": {
         "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8,
                      "description": "제품 이름들. 낱말 하나는 2~20자. 띄어쓰기는 무시하고 찾는다"},
         "as_of": _AS_OF,
         "min_share": {"type": "number", "description": "매출 비중이 이 값(%) 이상인 회사만. 주력인 회사만 볼 때 쓴다"},
         "listed_only": {"type": "boolean", "description": "상장사만 (기본 true)"}},
         "required": ["keywords", "as_of"]}},
    {"name": "get_products",
     "description": "한 회사의 제품과 매출 비중을 표로 읽은 그대로 준다 (가장 나중 정기보고서의 '주요 제품 및 서비스' 표). "
                    "읽지 못한 회사는 read=false 로 나오고, 그때는 get_business 로 표의 글을 직접 읽는다.",
     "input_schema": {"type": "object", "properties": {"company_id": _COMPANY, "as_of": _AS_OF},
                      "required": ["company_id", "as_of"]}},
    {"name": "get_coverage",
     "description": "이 DB가 무엇을 언제부터 언제까지 모았고 무엇을 모으지 않았는지. 결과가 비었을 때 답하기 전에 확인한다.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _detail(attrs: dict | None, keys=ATTRS) -> dict:
    """공시에서 읽은 값 가운데 Agent에게 내줄 것. 정정 내역은 길어질 수 있어 앞의 여덟 줄, 칸마다 120자까지만 싣는다."""
    out = {k: (attrs or {})[k] for k in keys if (attrs or {}).get(k) not in (None, "", False)}
    if isinstance((attrs or {}).get("listed"), bool):   # 계열회사 표의 상장·비상장 구분
        out["listed"] = "상장" if attrs["listed"] else "비상장"
    if "changes" in out:
        out["changes"] = [[str(cell)[:120] for cell in row] for row in out["changes"][:8]]
    return out


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
            "note": "group 은 공정위 2026년 5월 지정 대규모기업집단 이름입니다. group 이 null 이면 지정 집단 소속이 아닙니다"
                    if found else "원장에 없는 이름입니다. 상장사와 그 계열회사만 들어 있습니다"}


def _edge(db, edge: dict) -> dict:
    subject = db.get(Company, edge["subject_id"]) if edge["subject_id"] else None
    obj = db.get(Company, edge["object_id"]) if edge["object_id"] else None
    value = edge["value"]
    return {"type": query.LABELS[edge["type"]],
            "subject": _brief(subject) or {"company_id": None, "name": edge["subject_name_raw"],
                                           "note": "원장에 없는 주주 (개인, 정부, 비상장·해외 법인 등. 이름만 있음)"},
            "object": _brief(obj) or {"company_id": None, "name": edge["object_name_raw"], "note": "원장에 없는 상대 (이름만 있음)"},
            "value": None if value is None else (float(value) if edge["unit"] == "pct" else int(value)),
            "unit": {"pct": "%", "krw": "원"}.get(edge["unit"]),
            "as_of_date": edge["as_of_date"] and edge["as_of_date"].isoformat(),
            "disclosed_date": edge["disclosed_date"].isoformat(),
            "disclosed_by": {"subject": "주체가 공시", "object": "상대가 공시", "both": "양쪽 공시에서 확인"}[edge["disclosed_by"]],
            "stale": edge["stale"] or None,
            "superseded_on": edge["invalidated_date"] and edge["invalidated_date"].isoformat(),
            "detail": _detail(edge["attrs"]),
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
    unknown_listing = None
    if listed_only:
        other = "object" if direction != "in" else "subject"
        # 원장에 없는 상대라도 보고서가 스스로 '상장'이라고 적었으면 남긴다 (해외 상장사가 여기에 든다)
        kept = [r for r in rows if r[other].get("market") in ("유가증권", "코스닥", "코넥스") or r["detail"].get("listed") == "상장"]
        unknown_listing = sum(1 for r in rows if r not in kept and r[other]["company_id"] is None and "listed" not in r["detail"])
        rows = kept
    if rel_type == "equity":   # 지분은 큰 것부터
        rows.sort(key=lambda r: (-(r["value"] or 0), r["subject"]["name"] or "", r["object"]["name"] or ""))
    else:
        rows.sort(key=lambda r: (r["disclosed_date"], r["rcept_no"][0], r["object"]["name"]))
    sources = query.coverage(db)
    summary = None
    if rel_type == "affiliate" and direction == "out":
        listed = sum(1 for r in rows if r["detail"].get("listed") == "상장")
        report = db.scalars(select(Document).where(Document.company_id == company.company_id, Document.doc_type == "annual",
                                                   Document.rcept_dt <= when).order_by(Document.rcept_no.desc()).limit(1)).first()
        self_listed = MARKETS.get(company.corp_cls) in ("유가증권", "코스닥", "코넥스")
        summary = ({"self": f"{company.name} 자신은 {'상장사' if self_listed else '비상장사'}이고 아래 relations 목록에는 들어 있지 않습니다",
                    "excluding_self": {"listed": listed, "unlisted": len(rows) - listed},
                    "including_self": {"listed": listed + self_listed, "unlisted": len(rows) - listed + (not self_listed)},
                    "note": "보고서의 계열회사 표는 보통 자신을 넣어 셉니다. 상장·비상장 수를 물으면 including_self 를 답하고, "
                            "자신을 뺀 수(excluding_self)도 함께 밝히세요. 상장으로 분류된 회사를 물으면 자신도 듭니다"}
                   if rows else
                   {"listed": 0, "unlisted": 0,
                    "note": f"사업보고서({report.rcept_no})를 읽었고 계열회사 표에 올라온 회사가 없습니다. 보고서 기준으로 계열회사가 없다고 답할 수 있습니다"}
                   if report else None)
    return {"company": _brief(company), "as_of": when.isoformat(), "total": len(rows), "truncated": len(rows) > LIMIT,
            "affiliate_summary": summary,
            "relations": rows[:LIMIT],
            "fields": FIELDS,
            "unknown_listing": unknown_listing and f"원장에 없는 상대 {unknown_listing}곳은 상장 여부를 알 수 없어 뺐습니다. "
                                                   "해외 상장사일 수 있으니 listed_only 없이 다시 조회해 이름을 확인하세요",
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
                                   "unit": r.value_unit, "kind": query.LABELS[r.rel_type],
                                   "as_of_date": r.as_of_date and r.as_of_date.isoformat(),
                                   **_detail(r.attrs, ATTRS + ("withdrawn", "before_withdrawal", "original_filed",
                                                               "terminated_filing", "withdrawn_filing"))}
                                  for r in relations if r.rel_type not in ("equity", "affiliate")][:5],
                     "url": DART_LINK + doc.rcept_no})
    return {"company": _brief(company), "as_of": when.isoformat(), "total": len(rows), "truncated": len(rows) > LIMIT,
            "filings": rows[-LIMIT:],
            "fields": FIELDS,
            "note": "공급계약과 취득·처분 결정은 2024-01 이후 공시만, 사업보고서는 2023~2025 사업연도만 있습니다"}


def find_paths(db, from_company_id, to_company_id, as_of) -> dict:
    start, goal, when = _company(db, from_company_id), _company(db, to_company_id), _date(as_of, "as_of", required=True)
    found = query.paths(db, start.company_id, goal.company_id, when)
    return {"from": _brief(start), "to": _brief(goal), "as_of": when.isoformat(), "total": len(found),
            "paths": [[_edge(db, e) for e in path] for path in found[:10]],
            "note": None if found else "3단계 안에서 잇는 공시된 관계가 없습니다"}


def _in_industry(company: Company, prefix: str | None) -> bool:
    # 공정위 자료의 업종코드는 "C30121"처럼 대분류 글자가 앞에 붙는다
    return not prefix or re.sub(r"^[A-Z]", "", company.induty_code or "").startswith(prefix.strip())


def list_companies(db, industry: str | None = None, group: str | None = None, listed_only: bool = True) -> dict:
    if not industry and not group:
        raise ToolError("industry 나 group 중 하나는 주어야 합니다")
    conditions = [Company.corp_cls.in_(LISTED)] if listed_only else []
    if group:
        conditions.append(Company.ftc_group == group)
    found = sorted((c for c in db.scalars(select(Company).where(*conditions)) if _in_industry(c, industry)),
                   key=lambda c: (c.name, c.company_id))
    return {"total": len(found), "truncated": len(found) > 300,
            "companies": [{**_brief(c), "industry_code": c.induty_code} for c in found[:300]],
            "note": None if found else "조건에 맞는 기업이 없습니다. group 은 find_company 결과의 group 값 그대로 써야 합니다"}


def find_disclosers(db, as_of, rel_type: str, disclosed_from, disclosed_to, industry: str | None = None,
                    group: str | None = None, counterparty_id=None, counterparty_group: str | None = None,
                    listed_only: bool = True) -> dict:
    when = _date(as_of, "as_of", required=True)
    start, end = _date(disclosed_from, "disclosed_from", required=True), _date(disclosed_to, "disclosed_to", required=True)
    if rel_type not in ("supply_contract", "supply_termination", "stake_acquisition", "stake_disposal"):
        raise ToolError("rel_type 은 supply_contract, supply_termination, stake_acquisition, stake_disposal 중 하나입니다")
    target = aliased(Company)
    conditions = [Relation.rel_type == rel_type, Relation.retired_at.is_(None), Relation.disclosed_date >= start,
                  Relation.disclosed_date <= min(end, when)]
    if counterparty_id is not None:
        conditions.append(Relation.object_company_id == _company(db, counterparty_id).company_id)
    statement = select(Relation, Document.is_correction).join(Document, Document.rcept_no == Relation.rcept_no)
    if counterparty_group:
        statement = statement.join(target, target.company_id == Relation.object_company_id)
        conditions.append(target.ftc_group == counterparty_group)
    by_company: dict[int, dict] = {}
    for relation, is_correction in db.execute(statement.where(*conditions)):
        filer = db.get(Company, relation.subject_company_id)
        if (listed_only and filer.corp_cls not in LISTED) or (group and filer.ftc_group != group) or not _in_industry(filer, industry):
            continue
        entry = by_company.setdefault(filer.company_id, {**_brief(filer), "new": set(), "corrections": set(), "counterparties": set()})
        entry["corrections" if is_correction else "new"].add(relation.rcept_no)
        entry["counterparties"].add(relation.object_name_raw)
    rows = sorted(by_company.values(), key=lambda e: (e["name"], e["company_id"]))
    for entry in rows:
        new, corrections = sorted(entry["new"]), sorted(entry["corrections"])
        entry.update(new=len(new), corrections=len(corrections), rcept_no=(new + corrections)[:12],
                     counterparties=sorted(entry["counterparties"])[:8])
    return {"as_of": when.isoformat(), "period": [start.isoformat(), end.isoformat()], "total": len(rows),
            "with_new_filing": sum(1 for e in rows if e["new"]), "truncated": len(rows) > 200, "companies": rows[:200],
            "note": "new 는 정정이 아닌 공시, corrections 는 정정 공시의 건수입니다. 상대를 가린 공시는 counterparty 조건에 잡히지 않습니다"}


def get_coverage(db) -> dict:
    covered = query.coverage(db)
    return {"notice": covered["notice"], "sources": covered["sources"], "group": covered["sources"]["group"],
            "relations": {v["label"]: {"first_disclosed": str(v["first_disclosed"]), "last_disclosed": str(v["last_disclosed"]),
                                       "rows": v["rows"]} for v in covered["relations"].values()},
            "business": "사업 내용(search_business, get_business)과 제품(find_by_product, get_products)은 상장사의 2025 사업보고서와 2026 반기보고서에서 왔다. "
                        "제품은 매출 비중 표를 읽을 수 있었던 회사만 있다",
            "not_collected": ["최대주주와 특수관계인이 아닌 주주(5% 이상 주주, 소액주주)", "개인이 가진 다른 회사 지분",
                              "분기보고서", "반기보고서의 지분·계열 표", "주요 고객(사업보고서 본문 서술)", "뉴스", "주가",
                              "2024-01 이전의 공급계약과 취득·처분 결정"]}


_SECTION_WEIGHT = {1: 3, 2: 3, 4: 2, 6: 2}   # 사업 개요와 제품 표에 나온 낱말을 더 무겁게 친다. 나머지는 1


def _latest_business(db, when: date, company_id: int | None = None):
    """조회 시점까지 나온 사업보고서 가운데 회사마다 가장 나중 것의 접수번호."""
    latest = select(BusinessSection.company_id, func.max(BusinessSection.rcept_no).label("rcept_no")).where(
        BusinessSection.disclosed_date <= when)
    if company_id is not None:
        latest = latest.where(BusinessSection.company_id == company_id)
    return latest.group_by(BusinessSection.company_id).subquery()


def _report_name(db, rcept_no: str) -> str | None:
    """어느 보고서의 글인지. 예: "반기보고서 (2026.06)". 반기보고서의 매출은 반년 치라는 것을 읽는 쪽이 알아야 한다."""
    doc = db.get(Document, rcept_no)
    return doc and doc.report_nm.strip()


def search_business(db, keywords, as_of, listed_only: bool = True) -> dict:
    when = _date(as_of, "as_of", required=True)
    words = list(dict.fromkeys(str(w).strip() for w in (keywords if isinstance(keywords, list) else [keywords]) if str(w).strip()))
    if not words or any(not 2 <= len(w) <= 15 for w in words):
        raise ToolError("keywords 는 2~15자인 낱말의 목록입니다. 문장이 아니라 제품·서비스 이름을 넣으세요")
    words = words[:8]
    latest = _latest_business(db, when)
    rows = db.execute(select(BusinessSection).join(latest, BusinessSection.rcept_no == latest.c.rcept_no).where(
        or_(*[BusinessSection.text.contains(w, autoescape=True) for w in words]))).scalars().all()
    found: dict[int, dict] = {}
    for row in rows:
        entry = found.setdefault(row.company_id, {"score": 0, "matched": {}, "snippets": [], "rcept_no": row.rcept_no,
                                                  "bsns_year": row.bsns_year})
        weight = _SECTION_WEIGHT.get(row.section_no, 1)
        for word in words:
            count = row.text.count(word)
            if not count:
                continue
            entry["score"] += weight * min(count, 5)
            entry["matched"][word] = entry["matched"].get(word, 0) + count
            at = row.text.find(word)
            entry["snippets"].append((weight, {"section": row.title, "keyword": word,
                                               "text": " ".join(row.text[max(0, at - 90):at + 130].split())}))
    companies = {c.company_id: c for c in db.scalars(select(Company).where(Company.company_id.in_(list(found))))} if found else {}
    ranked = sorted(((i, e) for i, e in found.items()
                     if not listed_only or MARKETS.get(companies[i].corp_cls) in ("유가증권", "코스닥", "코넥스")),
                    key=lambda x: (-len(x[1]["matched"]), -x[1]["score"], companies[x[0]].name))
    out = []
    for company_id, entry in ranked[:30]:
        snippets, seen = [], set()
        for _, snippet in sorted(entry["snippets"], key=lambda s: -s[0]):
            if (snippet["section"], snippet["keyword"]) not in seen and len(snippets) < 3:
                seen.add((snippet["section"], snippet["keyword"]))
                snippets.append(snippet)
        out.append({**_brief(companies[company_id]), "matched": entry["matched"], "snippets": snippets,
                    "report": _report_name(db, entry["rcept_no"]), "rcept_no": entry["rcept_no"]})
    return {"as_of": when.isoformat(), "keywords": words, "total": len(ranked), "truncated": len(ranked) > 30, "companies": out,
            "note": "보고서 글에 낱말이 나온 회사입니다. matched 는 낱말별로 나온 횟수이고, 낱말이 나왔다고 그 사업이 주력이라는 뜻은 아닙니다. "
                    "주력인지는 get_business 의 매출 비중 표로 확인하고, 확인하지 않은 회사는 '보고서에 언급이 있다'고만 말하세요"
                    if out else "이 낱말이 나온 사업보고서가 없습니다. 다른 이름(제품명, 원재료명)으로 다시 찾아 보세요"}


def get_business(db, company_id, as_of, sections=None) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    wanted = [int(n) for n in (sections or [1, 2])]
    latest = _latest_business(db, when, company.company_id)
    rows = db.execute(select(BusinessSection).join(latest, BusinessSection.rcept_no == latest.c.rcept_no)
                      .order_by(BusinessSection.section_no)).scalars().all()
    if not rows:
        return {"company": _brief(company), "sections": [],
                "note": "이 회사의 '사업의 내용'은 DB에 없습니다. 상장사의 2025 사업보고서와 2026 반기보고서만 담았습니다"}
    limit = 6000
    return {"company": _brief(company), "report": _report_name(db, rows[0].rcept_no), "rcept_no": rows[0].rcept_no,
            "disclosed_date": rows[0].disclosed_date.isoformat(),
            "available": [{"section": r.section_no, "title": r.title, "chars": len(r.text)} for r in rows],
            "sections": [{"section": r.section_no, "title": r.title, "text": r.text[:limit],
                          "cut": len(r.text) > limit or bool(r.truncated)} for r in rows if r.section_no in wanted],
            "note": "보고서에 적힌 글 그대로입니다. 연결 기준인지 별도 기준인지, 단위가 무엇인지는 표의 머리말을 따릅니다. "
                    "report 가 반기·분기보고서이면 매출액은 그 기간(반년·누적) 치이므로, 금액을 옮길 때 어느 보고서의 값인지 밝히세요"}


def _latest_products(db, when: date, company_id: int | None = None):
    """조회 시점까지 나온 보고서 가운데 회사마다 제품 표를 읽은 가장 나중 것의 접수번호."""
    latest = select(Product.company_id, func.max(Product.rcept_no).label("rcept_no")).where(Product.disclosed_date <= when)
    if company_id is not None:
        latest = latest.where(Product.company_id == company_id)
    return latest.group_by(Product.company_id).subquery()


def _product_row(row: Product) -> dict:
    return {"segment": row.segment, "name": row.name, "share_pct": float(row.share_pct), "std_names": row.std_names or []}


_PRODUCT_NOTE = ("name 과 segment 는 보고서 표에 적힌 그대로, share_pct 는 그 줄이 매출에서 차지하는 비중(%)입니다. "
                 "std_names 는 모델이 붙인 표준 이름이라 틀릴 수 있으니 답에는 표에 적힌 이름을 옮기세요. "
                 "한 줄에 여러 제품이 함께 적혀 있으면 share_pct 는 그 줄 전체의 비중이지 그 제품만의 비중이 아닙니다")


def find_by_product(db, keywords, as_of, min_share=None, listed_only: bool = True) -> dict:
    when = _date(as_of, "as_of", required=True)
    words = list(dict.fromkeys(str(w).strip() for w in (keywords if isinstance(keywords, list) else [keywords]) if str(w).strip()))
    if not words or any(not 2 <= len(w) <= 20 for w in words):
        raise ToolError("keywords 는 2~20자인 제품 이름의 목록입니다")
    squeeze = lambda text: re.sub(r"\s", "", text or "").lower()
    keys = [squeeze(w) for w in words[:8]]
    latest = _latest_products(db, when)
    found: dict[int, dict] = {}
    names: dict[str, set] = {}
    for row in db.scalars(select(Product).join(latest, Product.rcept_no == latest.c.rcept_no).order_by(Product.rcept_no, Product.row_no)):
        if row.share_pct <= 0:
            continue
        matched = [name for name in row.std_names or () if any(key in squeeze(name) for key in keys)]
        if not matched and not any(key in squeeze(row.name) or key in squeeze(row.segment) for key in keys):
            continue
        entry = found.setdefault(row.company_id, {"share_pct": 0.0, "rows": [], "rcept_no": row.rcept_no, "matched_products": []})
        entry["share_pct"] = round(min(100.0, entry["share_pct"] + float(row.share_pct)), 2)
        entry["rows"].append(_product_row(row))
        for name in matched:
            names.setdefault(name, set()).add(row.company_id)
            if name not in entry["matched_products"]:
                entry["matched_products"].append(name)
    companies = {c.company_id: c for c in db.scalars(select(Company).where(Company.company_id.in_(list(found))))} if found else {}
    floor = float(min_share) if min_share is not None else 0.0
    ranked = sorted(((i, e) for i, e in found.items() if e["share_pct"] >= floor
                     and (not listed_only or MARKETS.get(companies[i].corp_cls) in ("유가증권", "코스닥", "코넥스"))),
                    key=lambda x: (-x[1]["share_pct"], companies[x[0]].name))
    shown = {i for i, _ in ranked}
    out = [{**_brief(companies[i]), "share_pct": e["share_pct"], "rows": e["rows"][:5], "matched_products": e["matched_products"],
            "report": _report_name(db, e["rcept_no"]), "rcept_no": e["rcept_no"]} for i, e in ranked[:LIMIT]]
    return {"as_of": when.isoformat(), "keywords": words[:8], "total": len(ranked), "truncated": len(ranked) > LIMIT, "companies": out,
            "standard_names": sorted(({"name": name, "companies": len(ids & shown)} for name, ids in names.items() if ids & shown),
                                     key=lambda x: (-x["companies"], x["name"]))[:20],
            "fields": "회사의 share_pct 는 걸린 줄의 비중을 더한 값입니다. " + _PRODUCT_NOTE,
            "note": "제품 표를 읽을 수 있었던 회사만 나옵니다. 표를 읽지 못한 회사, 제품 표가 없는 회사(금융업 등)는 빠져 있으니 "
                    "빠짐없이 찾아야 하면 search_business 로도 찾으세요" if out else
                    "이 이름이 제품 표에 나온 회사가 없습니다. 다른 표기(영문 약어, 상위 제품 이름)로 다시 찾거나 search_business 로 보고서 글에서 찾으세요"}


def get_products(db, company_id, as_of) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    latest = _latest_products(db, when, company.company_id)
    rows = db.scalars(select(Product).join(latest, Product.rcept_no == latest.c.rcept_no).order_by(Product.row_no)).all()
    if not rows:
        return {"company": _brief(company), "read": False, "products": [],
                "note": "이 회사의 제품 표는 읽지 못했거나 없습니다. 제품이 없다는 뜻이 아닙니다. get_business 로 '주요 제품 및 서비스'의 글을 직접 읽으세요"}
    return {"company": _brief(company), "read": True, "report": _report_name(db, rows[0].rcept_no), "rcept_no": rows[0].rcept_no,
            "disclosed_date": rows[0].disclosed_date.isoformat(), "total": len(rows),
            "products": [_product_row(row) for row in rows], "fields": _PRODUCT_NOTE}


FUNCTIONS = {"find_by_product": find_by_product, "get_products": get_products,
             "search_business": search_business, "get_business": get_business, "find_company": find_company, "get_relations": get_relations, "get_filings": get_filings,
             "list_companies": list_companies, "find_disclosers": find_disclosers,
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
