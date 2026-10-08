"""화면이 부르는 서버. 조회 계층(query.py)만 거쳐 DB를 읽는다. 읽기 전용이다.

실행: uvicorn company_graph.api:app --port 8000
"""
from collections import Counter, defaultdict
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select

from . import query
from .db import Company, Document, Relation, get_engine, session
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


@app.get("/api/meta")
def meta(db=Depends(get_db)):
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
