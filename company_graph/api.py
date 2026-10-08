"""화면이 부르는 서버. 조회 계층(query.py)만 거쳐 DB를 읽는다. 읽기 전용이다.

실행: uvicorn company_graph.api:app --port 8000
"""
import hashlib
import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from . import query
from .db import AskLog, Base, Company, Document, Relation, get_engine, session
from .stages import sector, stage

app = FastAPI(title="기업 관계 그래프")
_engine = get_engine()
GRAPH_TYPES = ("equity", "supply_contract", "affiliate")


def get_db():
    with session(_engine) as db:
        yield db


LISTED = ("Y", "K", "N")
MARKETS = {"Y": "유가증권", "K": "코스닥", "N": "코넥스"}


def categories(db) -> dict:
    """첫 화면을 나눠 볼 분류와 분류마다의 상장사 수."""
    listed = db.scalars(select(Company).where(Company.corp_cls.in_(LISTED))).all()
    count = lambda key: sorted(Counter(filter(None, map(key, listed))).items(), key=lambda x: (-x[1], x[0]))
    return {"market": [{"name": MARKETS[k], "count": n} for k, n in count(lambda c: c.corp_cls)],
            "sector": [{"name": k, "count": n} for k, n in count(lambda c: sector(c.induty_code))],
            "group": [{"name": k, "count": n} for k, n in count(lambda c: c.ftc_group)]}


def scope_members(db, scope: str) -> tuple[list[int], str]:
    """scope → (기업 번호, 관계를 고르는 방식). within 은 이 기업들끼리만, both 는 이 기업들과 그 상대까지."""
    kind, _, name = scope.partition(":")
    if kind == "focus":
        return list(db.scalars(select(Company.company_id).where(Company.in_scope))), "both"
    listed = db.scalars(select(Company).where(Company.corp_cls.in_(LISTED))).all()
    if kind == "market":
        return [c.company_id for c in listed if MARKETS[c.corp_cls] == name], "within"
    if kind == "sector":
        return [c.company_id for c in listed if sector(c.induty_code) == name], "both"
    if kind == "group":   # 집단은 비상장 계열사까지
        return list(db.scalars(select(Company.company_id).where(Company.ftc_group == name))), "both"
    return [c.company_id for c in listed], "within"


def company_json(c: Company) -> dict:
    return {"id": c.company_id, "name": c.name, "stock_code": c.stock_code, "listed": c.corp_cls in ("Y", "K"),
            "group": c.ftc_group, "stage": stage(c.induty_code), "sector": sector(c.induty_code),
            "market": MARKETS.get(c.corp_cls), "in_scope": c.in_scope, "corp_code": c.corp_code}


def edge_json(db, e: dict) -> dict:
    name = lambda i: db.get(Company, i).name if i else None
    return {"type": e["type"], "label": query.LABELS[e["type"]],
            "subject_id": e["subject_id"], "subject": name(e["subject_id"]) or e["subject_name_raw"],
            "object_id": e["object_id"], "object": name(e["object_id"]) or e["object_name_raw"],
            "value": float(e["value"]) if e["value"] is not None else None, "unit": e["unit"],
            "as_of_date": e["as_of_date"], "disclosed_date": e["disclosed_date"],
            "title": e["attrs"].get("title") or e["attrs"].get("purpose"), "joint_parties": e["attrs"].get("joint_parties"),
            "pct_after": e["attrs"].get("pct_after"),
            "trust_tier": e["trust_tier"], "disclosed_by": e["disclosed_by"], "stale": e["stale"],
            "evidence": [{"rcept_no": r, "url": query.DART_VIEWER + r} for r in e["evidence"]]}


def build_graph(db, edges: list[dict], focus: set[int] = frozenset()) -> dict:
    """선 목록을 화면용 점·선으로 바꾼다. 같은 두 기업 사이의 같은 종류 관계는 선 하나로 묶는다."""
    bundles = defaultdict(list)
    for e in edges:
        if e["object_id"] and e["subject_id"] and e["object_id"] != e["subject_id"]:
            bundles[(e["subject_id"], e["object_id"], e["type"])].append(e)
    links = []
    for (source, target, rel_type), group in bundles.items():
        link = {"source": source, "target": target, "type": rel_type, "count": len(group)}
        if rel_type == "equity":
            link["value"] = float(group[0]["value"])
            link["label"] = f"지분 {link['value']:.2f}%"
        elif rel_type == "supply_contract":
            # 상대가 둘인 계약은 줄마다 전체 금액이 들어 있지만, 여기서는 한 쌍만 보므로 겹쳐 세지 않는다
            link["value"] = float(sum(e["value"] or 0 for e in group))
            link["label"] = f"공급계약 {len(group)}건 · {link['value'] / 1e8:,.0f}억 원"
        else:
            link["label"] = "계열"
        links.append(link)
    ids = {i for link in links for i in (link["source"], link["target"])} | set(focus)
    degree = defaultdict(int)
    for link in links:
        degree[link["source"]] += 1
        degree[link["target"]] += 1
    nodes = [{**company_json(c), "degree": degree[c.company_id], "focus": c.company_id in focus}
             for c in db.scalars(select(Company).where(Company.company_id.in_(ids)))] if ids else []
    return {"nodes": nodes, "links": links}


def parse_types(types: str | None) -> list[str]:
    chosen = [t for t in (types or "").split(",") if t in GRAPH_TYPES]
    return chosen or list(GRAPH_TYPES)


_meta_cache: dict = {}
META_TTL = 600   # 초. 수집은 하루에 한 번이라 건수를 요청마다 다시 셀 필요가 없다


@app.get("/api/health")
def health(db=Depends(get_db)):
    """배포 환경의 예약 작업이 하루 한 번 부른다. 무료 DB는 일주일 동안 접속이 없으면 멈추기 때문이다."""
    return {"ok": db.scalar(select(func.count()).select_from(Company)) > 0}


@app.get("/api/meta")
def meta(db=Depends(get_db)):
    if _meta_cache and time.time() - _meta_cache["at"] < META_TTL:
        return _meta_cache["value"]
    _meta_cache.update(at=time.time(), value=_meta(db))
    return _meta_cache["value"]


def _meta(db) -> dict:
    first, last = db.execute(select(func.min(Relation.disclosed_date), func.max(Relation.disclosed_date))).one()
    counts = {k: v for k, v in db.execute(select(Relation.rel_type, func.count())
                                         .where(Relation.retired_at.is_(None)).group_by(Relation.rel_type)).all()}
    return {"first_date": first, "last_date": last, "today": date.today(),
            "companies": db.scalar(select(func.count()).select_from(Company)),
            "in_scope": db.scalar(select(func.count()).select_from(Company).where(Company.in_scope)),
            "documents": db.scalar(select(func.count()).select_from(Document)),
            "relations": {query.LABELS[k]: v for k, v in counts.items()}, "coverage": query.coverage(db),
            "categories": categories(db)}


@app.get("/api/companies")
def companies(q: str = Query(min_length=1), db=Depends(get_db)):
    return [company_json(c) for c in query.find_companies(db, q, limit=12)]


@app.get("/api/overview")
def overview(as_of: date, types: str | None = None, scope: str = "listed", db=Depends(get_db)):
    """첫 화면의 그래프. 계열은 선이 너무 많아 그리지 않고 점의 색(집단)으로 보여 준다.

    scope: listed(상장사끼리), market:유가증권, sector:자동차, group:삼성 처럼 분류를 고르거나, focus(수집을 시작한 자동차 가치사슬).
    """
    chosen = [t for t in parse_types(types) if t != "affiliate"]
    if not chosen:
        return build_graph(db, [])
    ids, direction = scope_members(db, scope)
    if not ids:
        return build_graph(db, [])
    return build_graph(db, query.relations(db, as_of, company_ids=ids, rel_types=chosen, direction=direction))


@app.get("/api/graph")
def graph(center: int, as_of: date, types: str | None = None, hops: int = Query(1, ge=1, le=2), db=Depends(get_db)):
    """한 기업을 중심으로 hops 단계까지."""
    if db.get(Company, center) is None:
        raise HTTPException(404, "기업을 찾지 못했습니다")
    chosen = parse_types(types)
    seen, frontier, edges = {center}, {center}, []
    for _ in range(hops):
        found = query.relations(db, as_of, company_ids=frontier, rel_types=[t for t in chosen if t != "affiliate"])
        edges += found
        frontier = {i for e in found for i in (e["subject_id"], e["object_id"]) if i} - seen
        seen |= frontier
    if "affiliate" in chosen:
        # 계열은 중심 기업의 집단만 그린다. 단계를 넓히면 집단마다 별 모양이 겹쳐 읽을 수 없다
        edges += query.group_members(db, center, as_of)
    return build_graph(db, edges, focus={center})


@app.get("/api/company/{company_id}")
def company(company_id: int, as_of: date, db=Depends(get_db)):
    found = db.get(Company, company_id)
    if found is None:
        raise HTTPException(404, "기업을 찾지 못했습니다")
    edges = query.relations(db, as_of, company_ids=[company_id],
                            rel_types=["equity", "supply_contract", *query.EVENT_TYPES])
    members = query.group_members(db, company_id, as_of)
    order = lambda e: (e["type"], e["subject_id"] != company_id, -(e["value"] or 0))
    return {"company": company_json(found), "as_of": as_of,
            "relations": [edge_json(db, e) for e in sorted(edges, key=order)],
            "group": {"count": len(members),
                      "source": edge_json(db, members[0])["evidence"] if members else [],
                      "members": sorted({m["object_name_raw"] for m in members})[:200]}}


# ---------- 한눈에 보는 칸: 최근 공시, 월별 공시 건수 ----------

_insight_cache: dict = {}
FEED_TYPES = ("supply_contract", "supply_termination", "stake_acquisition", "stake_disposal")


@app.get("/api/insights")
def insights(as_of: date, db=Depends(get_db)):
    """그 날짜까지의 최근 공시와, 최근에 바뀐 것(정정·해지·철회)."""
    hit = _insight_cache.get(as_of)
    if hit and time.time() - hit[0] < META_TTL:
        return hit[1]
    recent = []
    rows = db.scalars(select(Relation).where(Relation.rel_type.in_(FEED_TYPES), Relation.retired_at.is_(None),
                                             Relation.disclosed_date <= as_of, Relation.subject_company_id.is_not(None))
                      .order_by(Relation.disclosed_date.desc(), Relation.rcept_no.desc()).limit(60)).all()
    seen = set()
    for r in rows:
        if r.rcept_no in seen:
            continue
        seen.add(r.rcept_no)
        subject, obj = db.get(Company, r.subject_company_id), db.get(Company, r.object_company_id) if r.object_company_id else None
        recent.append({"rcept_no": r.rcept_no, "url": query.DART_VIEWER + r.rcept_no, "date": r.disclosed_date,
                       "type": r.rel_type, "label": query.LABELS[r.rel_type],
                       "subject_id": subject.company_id, "subject": subject.name,
                       "object_id": obj and obj.company_id, "object": obj.name if obj else r.object_name_raw,
                       "value": float(r.value_num) if r.value_num is not None else None,
                       "title": (r.attrs or {}).get("title") or (r.attrs or {}).get("purpose")})
        if len(recent) == 14:
            break
    # 최근에 바뀐 것: 정정, 해지, 철회. 한 번 공시된 사실이 그 뒤에 달라진 경우다
    changed = []
    documents = db.scalars(select(Document).where(
        Document.doc_type.in_(("supply_contract", "event")), Document.rcept_dt <= as_of,
        or_(Document.is_correction, Document.report_nm.like("%해지%"), Document.report_nm.like("%철회%")))
        .order_by(Document.rcept_no.desc()).limit(40)).all()
    for doc in documents:
        row = db.scalars(select(Relation).where(Relation.rcept_no == doc.rcept_no, Relation.retired_at.is_(None)).limit(1)).first()
        if row is None:
            continue
        attrs = row.attrs or {}
        kind = "철회" if "철회" in doc.report_nm else "해지" if "해지" in doc.report_nm else "정정"
        filer = db.get(Company, doc.company_id)
        changed.append({"rcept_no": doc.rcept_no, "url": query.DART_VIEWER + doc.rcept_no, "date": doc.rcept_dt, "kind": kind,
                        "company_id": filer.company_id, "company": filer.name,
                        "what": attrs.get("title") or attrs.get("purpose") or row.object_name_raw,
                        "reason": attrs.get("correction_reason") or attrs.get("reason")})
        if len(changed) == 14:
            break
    value = {"recent": recent, "changed": changed}
    _insight_cache[as_of] = (time.time(), value)
    return value


# ---------- Agent에게 묻기 (유료 API를 부른다) ----------

ASK_MODEL = os.environ.get("ASK_MODEL", "claude-haiku-5-5")
ASK_DAILY_LIMIT = int(os.environ.get("ASK_DAILY_LIMIT", "200"))        # 하루 전체 질문 수
ASK_VISITOR_LIMIT = int(os.environ.get("ASK_VISITOR_LIMIT", "10"))     # 방문자 한 명의 하루 질문 수
ASK_TYPES = {label: key for key, label in query.LABELS.items()}
_ask_ready = False


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=300)


def _visitor(request: Request) -> str:
    address = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    return hashlib.sha256((address + os.environ.get("ASK_SALT", "company-graph")).encode()).hexdigest()[:16]


def _asked_today(db, visitor: str | None = None) -> int:
    since = datetime.now() - timedelta(hours=24)
    conditions = [AskLog.asked_at >= since] + ([AskLog.visitor == visitor] if visitor else [])
    return db.scalar(select(func.count()).select_from(AskLog).where(*conditions)) or 0


@app.get("/api/ask/status")
def ask_status(request: Request, db=Depends(get_db)):
    """질문을 받을 수 있는 상태인지와 남은 횟수."""
    _prepare_ask()
    enabled = bool(os.environ.get("ANTHROPIC_API_KEY")) or os.name == "nt"
    return {"enabled": enabled, "model": ASK_MODEL,
            "left_today": max(0, ASK_DAILY_LIMIT - _asked_today(db)),
            "left_for_you": max(0, ASK_VISITOR_LIMIT - _asked_today(db, _visitor(request)))}


def _prepare_ask():
    global _ask_ready
    if not _ask_ready:
        Base.metadata.create_all(_engine, tables=[AskLog.__table__])
        _ask_ready = True


class _Found:
    """Agent가 도구로 조회한 기업과 관계를 모아, 화면이 그릴 그래프로 만든다."""

    def __init__(self):
        self.companies: set[int] = set()
        self.focus: set[int] = set()
        self.links: dict[tuple, dict] = {}

    def relation(self, item: dict):
        subject, obj = item["subject"].get("company_id"), item["object"].get("company_id")
        self.companies.update(i for i in (subject, obj) if i)
        if subject and obj and subject != obj:
            link = self.links.setdefault((subject, obj, ASK_TYPES.get(item["type"], "supply_contract")),
                                         {"count": 0, "value": None, "unit": item.get("unit"), "seen": set()})
            receipt = tuple(item.get("rcept_no") or ())
            if receipt in link["seen"]:
                return   # 같은 공시가 여러 도구 결과에 나왔다
            if not link["seen"]:
                link["count"], link["value"] = 0, None   # 건수만 알던 선을 실제 공시로 다시 센다
            link["seen"].add(receipt)
            link["count"] += 1
            if item.get("value") is not None:
                link["value"] = item["value"] if item.get("unit") == "%" else (link["value"] or 0) + item["value"]

    def take(self, name: str, arguments: dict, result: dict):
        if "error" in result:
            return
        if name == "find_company" and len(result["candidates"]) == 1:
            self.focus.add(result["candidates"][0]["company_id"])
        elif name == "get_relations":
            self.focus.add(result["company"]["company_id"])
            for item in result["relations"]:
                self.relation(item)
        elif name == "find_paths":
            self.focus.update((result["from"]["company_id"], result["to"]["company_id"]))
            for path in result["paths"]:
                for item in path:
                    self.relation(item)
        elif name == "find_disclosers":
            target = arguments.get("counterparty_id")
            for company in result["companies"][:80]:
                self.companies.add(company["company_id"])
                if target:
                    self.focus.add(target)
                    self.links.setdefault((company["company_id"], target, arguments["rel_type"]),
                                          {"count": company["new"] + company["corrections"], "value": None, "unit": None, "seen": set()})
        elif name == "list_companies":
            self.companies.update(c["company_id"] for c in result["companies"][:80])
        elif name == "get_filings":
            self.focus.add(result["company"]["company_id"])

    def graph(self, db) -> dict:
        ids = (self.companies | self.focus)
        ids = set(sorted(ids)[:200]) | self.focus
        links, degree = [], Counter()
        for (source, target, rel_type), link in self.links.items():
            if source not in ids or target not in ids:
                continue
            label = query.LABELS[rel_type]
            if rel_type == "equity" and link["value"] is not None:
                label = f"지분 {link['value']:.2f}%"
            elif link["value"]:
                label = f"{label} {link['count']}건 · {link['value'] / 1e8:,.0f}억 원"
            elif link["count"] > 1:
                label = f"{label} {link['count']}건"
            links.append({"source": source, "target": target, "type": rel_type, "count": link["count"],
                          "value": link["value"] if rel_type == "equity" else None, "label": label})
            degree[source] += 1
            degree[target] += 1
        nodes = [{**company_json(c), "degree": degree[c.company_id], "focus": c.company_id in self.focus}
                 for c in db.scalars(select(Company).where(Company.company_id.in_(ids)))] if ids else []
        return {"nodes": nodes, "links": links}


@app.post("/api/ask")
def ask(body: Ask, request: Request, db=Depends(get_db)):
    """질문 하나를 Agent에게 넘기고, 답과 함께 조회된 기업·관계를 그래프로 돌려준다."""
    from . import agent   # anthropic 패키지는 여기서만 필요하다

    _prepare_ask()
    visitor = _visitor(request)
    if _asked_today(db, visitor) >= ASK_VISITOR_LIMIT:
        raise HTTPException(429, f"한 사람이 하루에 물을 수 있는 횟수({ASK_VISITOR_LIMIT}번)를 다 썼습니다. 내일 다시 물어 주세요.")
    if _asked_today(db) >= ASK_DAILY_LIMIT:
        raise HTTPException(429, "오늘 받을 수 있는 질문을 다 받았습니다. 미리 돌려 둔 예시는 '평가' 탭에서 볼 수 있습니다.")
    question = " ".join(body.question.split())
    entry = AskLog(asked_at=datetime.now(), visitor=visitor, question=question[:300], ok=False)
    db.add(entry)
    db.commit()   # 답이 오기 전에 먼저 센다. 동시에 여러 번 눌러도 상한을 넘지 않게
    found = _Found()
    try:
        result = agent.answer(question, model=ASK_MODEL, as_of=date.today(), max_turns=8, on_result=found.take)
    except Exception as error:   # 키가 없거나 API가 실패한 경우. 방문자에게는 사정만 알린다
        raise HTTPException(503, "지금은 Agent가 답할 수 없습니다. 잠시 뒤에 다시 시도해 주세요.") from error
    entry.ok, entry.tokens, entry.seconds = True, sum(result["usage"].values()), result["seconds"]
    db.commit()
    # 출처: 답에 적힌 접수번호 가운데 조회 결과에 실제로 있었던 공시. 답의 글과 따로, 원장에서 다시 찾아 붙인다
    cited = [no for no in result["cited"] if no not in result["cited_not_in_results"]]
    documents = {d.rcept_no: d for d in db.scalars(select(Document).where(Document.rcept_no.in_(cited))).all()} if cited else {}
    sources = [{"rcept_no": no, "url": query.DART_VIEWER + no,
                "company": documents[no].company_id and db.get(Company, documents[no].company_id).name,
                "report": documents[no].report_nm.strip(), "filed": documents[no].rcept_dt.isoformat()}
               if no in documents else {"rcept_no": no, "url": query.DART_VIEWER + no, "company": None, "report": None, "filed": None}
               for no in cited]
    return {"question": question, "answer": result["answer"], "as_of": result["as_of"], "model": result["model"],
            "seconds": result["seconds"], "tokens": entry.tokens,
            "tools": [{"name": c["tool"], "input": c["input"], "total": c["total"], "error": c["error"]} for c in result["tool_calls"]],
            "unverified_citations": result["cited_not_in_results"],
            "sources": sources,
            "graph": found.graph(db),
            "left_for_you": max(0, ASK_VISITOR_LIMIT - _asked_today(db, visitor))}
