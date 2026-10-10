"""화면이 부르는 서버. 조회 계층(query.py)만 거쳐 DB를 읽는다. 읽기 전용이다.

실행: uvicorn company_graph.api:app --port 8000
"""
import hashlib
import hmac
import json
import os
import queue
import threading
import time
import zlib
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from . import ksic, query
from .display_names import group_key, group_label
from .db import AskLog, Base, BusinessSection, Company, Document, HotDay, Product, ProductCode, Relation, get_engine, session
from .product_families import FAMILY_FIELD
from .stages import sector, stage

app = FastAPI(title="기업 관계 그래프")

# 읽기만 하는 응답은 CDN 이 들고 있다가 바로 내준다. 첫 화면의 그래프(/api/overview)는 만드는 데 1~4초가 걸린다.
# 공시 자료는 DB를 다시 옮기고 배포할 때만 바뀌고(배포하면 CDN 의 것은 버려진다), 이슈 종목만 날마다 바뀌어 짧게 둔다.
# 사람마다 다른 응답(/api/ask…)과 실패한 응답에는 붙이지 않는다
_CACHED = {"/api/hot": "public, s-maxage=600, stale-while-revalidate=86400"}
_CACHE_DEFAULT = "public, s-maxage=86400, stale-while-revalidate=604800"


@app.middleware("http")
async def cache_reads(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if request.method == "GET" and response.status_code == 200 and path.startswith("/api/") and not path.startswith(("/api/ask", "/api/health")):
        response.headers["Cache-Control"] = next((v for k, v in _CACHED.items() if path.startswith(k)), _CACHE_DEFAULT)
    return response
_engine = get_engine()
GRAPH_TYPES = ("equity", "supply_contract", "affiliate", "stake_acquisition", "stake_disposal", "product")
DEFAULT_TYPES = ("equity", "supply_contract", "affiliate")


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
            "group": [{"name": k, "count": n} for k, n in count(lambda c: group_label(c.ftc_group))]}


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
        return list(db.scalars(select(Company.company_id).where(Company.ftc_group == group_key(name)))), "both"
    return [c.company_id for c in listed], "within"


def company_json(c: Company) -> dict:
    # name 은 대표 이름(종목명 등), legal_name 은 등기 이름
    return {"id": c.company_id, "name": c.label, "legal_name": c.name, "stock_code": c.stock_code,
            "listed": c.corp_cls in ("Y", "K"), "group": group_label(c.ftc_group), "stage": stage(c.induty_code), "sector": sector(c.induty_code),
            "market": MARKETS.get(c.corp_cls), "in_scope": c.in_scope, "corp_code": c.corp_code}


def edge_json(db, e: dict) -> dict:
    name = lambda i: db.get(Company, i).label if i else None
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
        elif rel_type == "affiliate":
            link["label"] = "계열"
        else:   # 취득·처분 결정
            link["value"] = float(sum(e["value"] or 0 for e in group))
            link["label"] = f"{query.LABELS[rel_type]} {len(group)}건 · {link['value'] / 1e8:,.0f}억 원"
        links.append(link)
    ids = {i for link in links for i in (link["source"], link["target"])} | set(focus)
    degree = defaultdict(int)
    for link in links:
        degree[link["source"]] += 1
        degree[link["target"]] += 1
    nodes = [{**company_json(c), "degree": degree[c.company_id], "focus": c.company_id in focus}
             for c in db.scalars(select(Company).where(Company.company_id.in_(ids)))] if ids else []
    return {"nodes": nodes, "links": links}


# ---------- 제품: 기업이 무엇을 파는가 ----------

_product_cache: dict = {}
SHARED_BY = 2       # 첫 화면에서는 이만큼의 기업이 함께 파는 제품만 점으로 둔다
SHARERS = 12        # 한 기업을 중심으로 볼 때, 제품 하나에 붙이는 다른 기업 수 (매출 비중이 큰 순)


def product_id(name: str) -> int:
    """제품 점의 번호. 기업 번호, 질문 결과의 낱말 점(-1, -2, …)과 겹치지 않는 음수이고, 이름이 같으면 늘 같다."""
    return -(zlib.crc32(name.encode()) + 1000)


def family_id(name: str) -> int:
    """제품군 점의 번호. 같은 이름의 제품 점과 겹치지 않게 앞에 표시를 붙여 만든다."""
    return product_id("제품군:" + name)


def product_index(db, as_of: date) -> dict[int, dict[str, dict]]:
    """기업 → {제품 이름: {share 매출 비중, raw 표에 적힌 이름들, family 제품군, split 나눈 값인가}}. 기업마다 그 시점까지 나온 가장 나중 보고서의 표만 쓴다.

    표의 한 줄에 제품이 여럿 적혀 있으면 보고서에는 줄 전체의 비중만 있다. 그 비중을 제품 수로 고르게 나눠 제품마다의 몫으로 삼는다.
    실제 몫은 알 수 없으므로 어림값이고(split), 화면에는 그렇게 표시한다. 나누지 않으면 한 줄의 여덟 제품이 모두 33%로 그려진다.
    """
    hit = _product_cache.get(as_of)
    if hit and time.time() - hit[0] < META_TTL:
        return hit[1]
    latest = (select(Product.company_id, func.max(Product.rcept_no).label("rcept_no"))
              .where(Product.disclosed_date <= as_of).group_by(Product.company_id).subquery())
    index: dict[int, dict[str, dict]] = defaultdict(dict)
    codes = {(c.family, c.name): c.ksic for c in db.scalars(select(ProductCode))}
    for row in db.scalars(select(Product).join(latest, Product.rcept_no == latest.c.rcept_no)):
        if row.share_pct is not None and row.share_pct <= 0:
            continue
        families = row.std_families or [None] * len(row.std_names or [])
        count = len(row.std_names or ())
        for name, family in zip(row.std_names or (), families):
            item = index[row.company_id].setdefault(name, {"share": 0.0, "raw": [], "family": family, "split": False,
                                                           "ksic": codes.get((family, name))})
            # 비중이 없는 줄(보고서가 밝히지 않음)은 0으로 둔다. 0 이하인 줄은 위에서 걸렀으므로, 0은 "비중을 모름"만 뜻한다
            item["share"] = min(100.0, item["share"] + float(row.share_pct or 0) / count)
            item["split"] = item["split"] or count > 1
            if row.name not in item["raw"]:
                item["raw"].append(row.name)
    _product_cache[as_of] = (time.time(), index)
    return index


def _point(number: int, name: str, kind: str, members: int, sector_name: str, focus: bool = False) -> dict:
    """기업이 아닌 점(제품, 제품군, 사업 낱말). 화면이 기업과 같은 모양으로 다루도록 빈 칸을 채워 둔다."""
    return {"id": number, "name": name, "kind": kind, "stock_code": None, "listed": False, "group": None, "stage": "",
            "sector": sector_name, "market": None, "in_scope": False, "degree": members, "focus": focus}


def add_products(db, graph: dict, as_of: date, company_ids, center: int | None = None) -> dict:
    """그래프에 제품군 점, 제품 점과 선을 더한다.

    기업 → 제품(매출 비중) → 제품군. 두 곳 이상이 함께 파는 제품만 점으로 두고, 혼자 파는 제품은 그 기업을 제품군에 바로 잇는다.
    그래서 같은 제품군의 회사는 제품 이름이 달라도 제품군 점에서 만난다.
    """
    index = product_index(db, as_of)
    if center is not None:
        # 중심 기업의 제품과 제품군, 그리고 같은 제품군의 다른 기업을 그 제품군 비중이 큰 순으로 붙인다
        mine = index.get(center, {})
        families = {item["family"] for item in mine.values() if item["family"] and item["family"] != "기타"}
        weight = lambda i: sum(item["share"] for item in index[i].values() if item["family"] in families)
        peers = sorted((i for i, items in index.items() if i != center and any(item["family"] in families for item in items.values())),
                       key=lambda i: -weight(i))
        chosen, per_family = {center}, Counter()
        for company_id in peers:
            own = {item["family"] for item in index[company_id].values()} & families
            if any(per_family[family] < SHARERS for family in own):
                chosen.add(company_id)
                per_family.update(own)
        members = {i: {n: item for n, item in index[i].items() if item["family"] in families or i == center} for i in chosen}
        shared_by = 1    # 중심 기업의 제품은 혼자 파는 것도 보여 준다
    else:
        members = {i: index[i] for i in set(company_ids) & index.keys()}
        shared_by = SHARED_BY
    makers: dict[str, set] = defaultdict(set)
    for company_id, items in members.items():
        for name in items:
            makers[name].add(company_id)
    shown = {name for name, ids in makers.items() if len(ids) >= shared_by and (center is None or center in ids or len(ids) >= SHARED_BY)}
    known = {node["id"]: node for node in graph["nodes"]}
    missing = set(members) - known.keys()
    if missing:
        for c in db.scalars(select(Company).where(Company.company_id.in_(missing))):
            known[c.company_id] = {**company_json(c), "degree": 0, "focus": False}
    links, family_members, product_family = graph["links"], defaultdict(set), {}
    for company_id, items in members.items():
        direct: dict[str, dict] = {}   # 혼자 파는 제품은 제품군에 바로: 제품군 → {share, 이름들}
        about = lambda item: (NO_SHARE if item["share"] <= 0 else
                              f"매출의 약 {item['share']:.1f}% (여러 제품이 적힌 줄의 비중을 제품 수로 나눈 어림값)" if item["split"]
                              else f"매출의 {item['share']:.1f}%")
        for name, item in items.items():
            family = item["family"] if item["family"] and item["family"] != "기타" else None
            if family:
                family_members[family].add(company_id)
            raw = ", ".join(item["raw"][:3])
            if name in shown:
                product_family[name] = family
                links.append({"source": company_id, "target": product_id(name), "type": "product", "count": 1,
                              "value": round(item["share"], 2), "label": f"{about(item)} · 보고서에 적힌 이름: {raw}"})
                known[company_id]["degree"] += 1
            elif family:
                slot = direct.setdefault(family, {"share": 0.0, "names": [], "split": False})
                slot["share"] = min(100.0, slot["share"] + item["share"])
                slot["split"] = slot["split"] or item["split"]
                slot["names"].append(name)
        for family, slot in direct.items():
            links.append({"source": company_id, "target": family_id(family), "type": "product", "count": len(slot["names"]),
                          "value": round(slot["share"], 2), "label": f"{about(slot)} · {', '.join(slot['names'][:4])}"})
            known[company_id]["degree"] += 1
    for name in sorted(shown):
        family = product_family.get(name)
        known[product_id(name)] = _point(product_id(name), name, "product", len(makers[name]), family or "제품")
        if family:
            links.append({"source": product_id(name), "target": family_id(family), "type": "family", "count": 1, "value": None,
                          "label": f"제품군 {family}"})
    for family, ids in sorted(family_members.items()):
        if len(ids) >= shared_by or center is not None:
            known[family_id(family)] = _point(family_id(family), family, "family", len(ids), FAMILY_FIELD.get(family, "제품군"))
    # 제품군 점이 없는 선(한 곳뿐인 제품군)은 뺀다
    graph["links"] = [link for link in links if link["target"] in known and link["source"] in known]
    graph["nodes"] = list(known.values())
    return graph


LEVELS = ("section", "division", "family", "product")


def class_id(code: str) -> int:
    """공식 분류(대분류, 중분류) 점의 번호."""
    return product_id("분류:" + code)


def _group_of(item: dict, level: str) -> tuple[int, str, str, str] | None:
    """제품 하나가 그 범주 크기에서 속하는 묶음: (점 번호, 이름, 점 종류, 설명). 묶을 데가 없으면 None."""
    if level == "family":
        family = item["family"]
        return (family_id(family), family, "family", FAMILY_FIELD.get(family, "제품군")) if family and family != "기타" else None
    code = item.get("ksic")
    key = code and (ksic.section(code) if level == "section" else code[:2])
    if not key:
        return None
    official = f"한국표준산업분류 {'대분류' if level == 'section' else '중분류'} {key} · {ksic.name(key)}"
    return class_id(key), ksic.short(key), "class", official


def add_groups(db, graph: dict, as_of: date, company_ids, level: str, center: int | None = None) -> dict:
    """그래프에 범주 점과 `기업 → 범주` 선을 더한다. 범주는 공식 분류의 대분류·중분류이거나 제품군이다.

    선의 값은 그 범주에 드는 제품들의 매출 비중을 더한 것이다. 제품 이름까지 보려면 add_products 를 쓴다.
    """
    index = product_index(db, as_of)
    grouped: dict[int, dict[int, dict]] = {}
    points: dict[int, tuple] = {}
    for company_id in (index.keys() if center is not None else set(company_ids) & index.keys()):
        for name, item in index[company_id].items():
            group = _group_of(item, level)
            if group is None:
                continue
            points[group[0]] = group
            slot = grouped.setdefault(company_id, {}).setdefault(group[0], {"share": 0.0, "names": [], "split": False})
            slot["share"] = min(100.0, slot["share"] + item["share"])
            slot["names"].append(name)
            slot["split"] = slot["split"] or item["split"]
    if center is not None:
        # 중심 기업의 범주와, 범주마다 그 비중이 큰 다른 기업 몇 곳
        mine = set(grouped.get(center, {}))
        chosen = {center}
        for number in mine:
            peers = sorted((i for i, slots in grouped.items() if i != center and number in slots), key=lambda i: -grouped[i][number]["share"])
            chosen.update(peers[:SHARERS])
        grouped = {i: {n: slot for n, slot in grouped[i].items() if n in mine} for i in chosen if i in grouped}
    members = Counter(number for slots in grouped.values() for number in slots)
    shown = {number for number, count in members.items() if count >= (1 if center is not None else SHARED_BY)}
    known = {node["id"]: node for node in graph["nodes"]}
    missing = {i for i, slots in grouped.items() if set(slots) & shown} - known.keys()
    if missing:
        for c in db.scalars(select(Company).where(Company.company_id.in_(missing))):
            known[c.company_id] = {**company_json(c), "degree": 0, "focus": False}
    for company_id, slots in grouped.items():
        for number, slot in slots.items():
            if number not in shown:
                continue
            about = NO_SHARE if slot["share"] <= 0 else (
                f"매출의 {'약 ' if slot['split'] else ''}{slot['share']:.1f}%" + (" (나눈 어림값 포함)" if slot["split"] else ""))
            graph["links"].append({"source": company_id, "target": number, "type": "product", "count": len(slot["names"]),
                                   "value": round(slot["share"], 2), "label": f"{about} · {', '.join(slot['names'][:4])}"})
            known[company_id]["degree"] += 1
    for number in sorted(shown):
        _, name, kind, about = points[number]
        known[number] = _point(number, name, kind, members[number], about)
    graph["nodes"] = list(known.values())
    return graph


def add_product_layer(db, graph: dict, as_of: date, company_ids, level: str, center: int | None = None) -> dict:
    if level == "product":
        return add_products(db, graph, as_of, company_ids, center=center)
    return add_groups(db, graph, as_of, company_ids, level if level in LEVELS else "family", center=center)


@app.get("/api/taxonomy")
def taxonomy(db=Depends(get_db)):
    """제품을 묶는 분류 전체: 대분류 > 중분류(공식 분류) > 제품군 > 제품. 데이터 페이지가 목록으로 보여 준다."""
    hit = _product_cache.get("taxonomy")
    if hit and time.time() - hit[0] < META_TTL:
        return hit[1]
    index = product_index(db, date.today())
    tree: dict = {}
    unsure = {(c.family, c.name) for c in db.scalars(select(ProductCode).where(ProductCode.unsure))}
    for company_id, items in index.items():
        for name, item in items.items():
            code, family = item.get("ksic"), item["family"] or "기타"
            if not code:
                continue
            division = tree.setdefault(ksic.section(code), {}).setdefault(code[:2], {})
            entry = division.setdefault(family, {}).setdefault(name, {"companies": set(), "ksic": code, "unsure": (family, name) in unsure})
            entry["companies"].add(company_id)
    out = []
    for section in sorted(tree):
        divisions = []
        for code in sorted(tree[section]):
            families = []
            for family, products in tree[section][code].items():
                companies = set().union(*(p["companies"] for p in products.values()))
                families.append({"name": family, "field": FAMILY_FIELD.get(family), "companies": len(companies),
                                 "products": sorted(({"name": n, "companies": len(p["companies"]), "ksic": p["ksic"], "ksic_name": ksic.name(p["ksic"]),
                                                      "unsure": p["unsure"]} for n, p in products.items()), key=lambda x: (-x["companies"], x["name"]))})
            divisions.append({"code": code, "name": ksic.name(code), "short": ksic.short(code),
                              "families": sorted(families, key=lambda f: (-f["companies"], f["name"]))})
        out.append({"code": section, "name": ksic.name(section), "divisions": divisions})
    value = {"sections": out, "source": "한국표준산업분류 제11차 개정 (통계청 고시, 2024-07-01 시행)"}
    _product_cache["taxonomy"] = (time.time(), value)
    return value


def _directory(db) -> list[dict]:
    """찾을 수 있는 제품, 제품군, 공식 분류의 목록과 그것을 파는 기업 수. 입력 칸의 추천에 쓴다."""
    hit = _product_cache.get("directory")
    if hit and time.time() - hit[0] < META_TTL:
        return hit[1]
    index = product_index(db, date.today())
    counts: dict[tuple, set] = defaultdict(set)
    for company_id, items in index.items():
        for name, item in items.items():
            counts[("product", name, name)].add(company_id)
            if item["family"] and item["family"] != "기타":
                counts[("family", item["family"], item["family"])].add(company_id)
            if item.get("ksic"):
                counts[("class", item["ksic"][:2], ksic.short(item["ksic"][:2]))].add(company_id)
    value = [{"kind": kind, "key": key, "name": name, "companies": len(ids)} for (kind, key, name), ids in counts.items()]
    _product_cache["directory"] = (time.time(), value)
    return value


@app.get("/api/suggest")
def suggest(q: str = Query(min_length=1, max_length=30), db=Depends(get_db)):
    """적는 글에 맞는 제품, 제품군, 공식 분류. Agent를 부르지 않고 바로 그래프로 갈 수 있게 한다."""
    squeeze = lambda text: "".join(text.split()).lower()
    key = squeeze(q)
    rank = {"family": 0, "class": 1, "product": 2}
    found = [d for d in _directory(db) if key in squeeze(d["name"])]
    found.sort(key=lambda d: (squeeze(d["name"]) != key, rank[d["kind"]], -d["companies"], d["name"]))
    return found[:8]


@app.get("/api/pick")
def pick(kind: str, key: str, as_of: date, db=Depends(get_db)):
    """제품 하나, 제품군 하나, 또는 공식 분류(중분류) 하나를 파는 기업 전부를 그 점에 이어 그린다."""
    index = product_index(db, as_of)
    if kind == "product":
        number, match = product_id(key), (lambda name, item: name == key)
    elif kind == "family":
        number, match = family_id(key), (lambda name, item: item["family"] == key)
    elif kind == "class" and ksic.valid(key) and key.isalpha():   # 대분류는 글자(C)라서 코드의 앞자리로는 못 찾는다
        number, match = class_id(key), (lambda name, item: bool(item.get("ksic")) and ksic.section(item["ksic"]) == key)
    elif kind == "class" and ksic.valid(key):
        number, match = class_id(key), (lambda name, item: (item.get("ksic") or "").startswith(key))
    else:
        raise HTTPException(404, "그런 제품이나 분류가 없습니다")
    members, family_of = {}, Counter()
    for company_id, items in index.items():
        hits = [(name, item) for name, item in items.items() if match(name, item)]
        if hits:
            members[company_id] = {"share": min(100.0, sum(item["share"] for _, item in hits)), "names": [name for name, _ in hits],
                                   "split": any(item["split"] for _, item in hits)}
            family_of.update(item["family"] for _, item in hits if item["family"])
    if not members:
        raise HTTPException(404, "그 시점에 이것을 파는 기업이 없습니다")
    nodes = [{**company_json(c), "degree": 1, "focus": False} for c in db.scalars(select(Company).where(Company.company_id.in_(members)))]
    if kind == "product":
        family = family_of.most_common(1)[0][0] if family_of else None
        point = _point(number, key, "product", len(members), family or "제품", focus=True)
    elif kind == "family":
        point = _point(number, key, "family", len(members), FAMILY_FIELD.get(key, "제품군"), focus=True)
    else:
        point = _point(number, ksic.short(key), "class", len(members), f"한국표준산업분류 {key} · {ksic.name(key)}", focus=True)
    links = [{"source": company_id, "target": number, "type": "product", "count": len(m["names"]), "value": round(m["share"], 2),
              "label": (NO_SHARE if m["share"] <= 0 else f"매출의 {'약 ' if m['split'] else ''}{m['share']:.1f}%") + f" · {', '.join(m['names'][:4])}"}
             for company_id, m in members.items()]
    if kind == "product" and point["sector"] != "제품":
        # 그 제품이 속한 제품군을 한 단계 위에 둔다. 같은 제품군의 다른 제품은 제품군을 눌러 넘어가서 본다
        family = point["sector"]
        peers = {i for i, items in index.items() if any(item["family"] == family for item in items.values())}
        nodes.append(_point(family_id(family), family, "family", len(peers), FAMILY_FIELD.get(family, "제품군")))
        links.append({"source": number, "target": family_id(family), "type": "family", "count": 1, "value": None, "label": f"제품군 {family}"})
    return {"nodes": nodes + [point], "links": links}


def products_json(db, company_id: int, as_of: date) -> dict | None:
    """그 시점까지 나온 보고서의 제품 표. 주요 제품 절은 있는데 표를 읽지 못한 회사는 read 가 False 다(지어내지 않는다)."""
    latest = db.scalar(select(func.max(Product.rcept_no)).where(Product.company_id == company_id, Product.disclosed_date <= as_of))
    if latest is None:
        has_section = db.scalar(select(func.count()).select_from(BusinessSection).where(
            BusinessSection.company_id == company_id, BusinessSection.disclosed_date <= as_of,
            BusinessSection.title.like("%주요 제품%")))
        return {"read": False} if has_section else None
    doc = db.get(Document, latest)
    rows = db.scalars(select(Product).where(Product.rcept_no == latest).order_by(Product.row_no)).all()
    codes = {(c.family, c.name): c.ksic for c in db.scalars(select(ProductCode))}
    code_of = lambda r: [codes.get((family, name)) for name, family in zip(r.std_names or [], r.std_families or [])]
    return {"read": True, "rcept_no": latest, "url": query.DART_VIEWER + latest, "report": doc.report_nm.strip() if doc else None,
            "read_by": "model" if any(r.read_by == "model" for r in rows) else "rule",
            "rows": [{"segment": r.segment, "name": r.name, "share": None if r.share_pct is None else float(r.share_pct), "std_names": r.std_names or [],
                      "families": r.std_families or [], "unsure": bool(r.unsure),
                      "ksic": [code and {"code": code, "name": ksic.name(code)} for code in code_of(r)]} for r in rows]}


def parse_types(types: str | None) -> list[str]:
    chosen = [t for t in (types or "").split(",") if t in GRAPH_TYPES]
    return chosen or list(DEFAULT_TYPES)


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
            "business": business_counts(db),
            "categories": categories(db)}


def business_counts(db) -> dict:
    """사업 내용과 제품을 얼마나 담았는지. 제품 표는 읽지 못한 회사가 있어 그 수를 같이 보여 준다."""
    companies = select(func.count(func.distinct(BusinessSection.company_id)))
    return {"sections": db.scalar(select(func.count()).select_from(BusinessSection)),
            "reports": db.scalar(select(func.count(func.distinct(BusinessSection.rcept_no)))),
            "companies": db.scalar(companies),
            "companies_with_product_section": db.scalar(companies.where(BusinessSection.title.like("%주요 제품%"))),
            "companies_with_products": db.scalar(select(func.count(func.distinct(Product.company_id)))),
            "product_rows": db.scalar(select(func.count()).select_from(Product))}


@app.get("/api/companies")
def companies(q: str = Query(min_length=1), db=Depends(get_db)):
    return [company_json(c) for c in query.find_companies(db, q, limit=12)]


@app.get("/api/overview")
def overview(as_of: date, types: str | None = None, scope: str = "listed", level: str = "family", db=Depends(get_db)):
    """첫 화면의 그래프. 계열은 선이 너무 많아 그리지 않고 점의 색(집단)으로 보여 준다.

    scope: listed(상장사끼리), market:유가증권, sector:자동차, group:삼성 처럼 분류를 고르거나, focus(수집을 시작한 자동차 가치사슬).
    """
    chosen = [t for t in parse_types(types) if t != "affiliate"]
    if not chosen:
        return build_graph(db, [])
    ids, direction = scope_members(db, scope)
    if not ids:
        return build_graph(db, [])
    relation_types = [t for t in chosen if t != "product"]
    graph = build_graph(db, query.relations(db, as_of, company_ids=ids, rel_types=relation_types, direction=direction)
                        if relation_types else [])
    return add_product_layer(db, graph, as_of, ids, level) if "product" in chosen else graph


@app.get("/api/graph")
def graph(center: int, as_of: date, types: str | None = None, hops: int = Query(1, ge=1, le=2), level: str = "product",
          db=Depends(get_db)):
    """한 기업을 중심으로 hops 단계까지."""
    if db.get(Company, center) is None:
        raise HTTPException(404, "기업을 찾지 못했습니다")
    chosen = parse_types(types)
    seen, frontier, edges = {center}, {center}, []
    for _ in range(hops):
        relation_types = [t for t in chosen if t not in ("affiliate", "product")]
        found = query.relations(db, as_of, company_ids=frontier, rel_types=relation_types) if relation_types else []
        edges += found
        frontier = {i for e in found for i in (e["subject_id"], e["object_id"]) if i} - seen
        seen |= frontier
    if "affiliate" in chosen:
        # 계열은 중심 기업의 집단만 그린다. 단계를 넓히면 집단마다 별 모양이 겹쳐 읽을 수 없다
        edges += query.group_members(db, center, as_of)
    graph = build_graph(db, edges, focus={center})
    return add_product_layer(db, graph, as_of, [center], level, center=center) if "product" in chosen else graph


def business_json(db, company_id: int, as_of: date) -> dict | None:
    """그 시점까지 나온 가장 나중 정기보고서의 사업 개요와 주요 제품. 보고서에 적힌 글 그대로다."""
    latest = db.scalar(select(func.max(BusinessSection.rcept_no)).where(BusinessSection.company_id == company_id,
                                                                        BusinessSection.disclosed_date <= as_of))
    if latest is None:
        return None
    rows = {r.section_no: r for r in db.scalars(select(BusinessSection).where(
        BusinessSection.rcept_no == latest, BusinessSection.section_no.in_((0, 1, 2))))}
    doc = db.get(Document, latest)
    overview, products = rows.get(1) or rows.get(0), rows.get(2)
    return {"rcept_no": latest, "url": query.DART_VIEWER + latest, "report": doc.report_nm.strip() if doc else None,
            "disclosed_date": doc.rcept_dt if doc else None,
            "overview": overview.text[:1500] if overview else None,
            "products": products.text[:2500] if products else None,
            "cut": bool((overview and len(overview.text) > 1500) or (products and len(products.text) > 2500))}


@app.get("/api/company/{company_id}")
def company(company_id: int, as_of: date, db=Depends(get_db)):
    found = db.get(Company, company_id)
    if found is None:
        raise HTTPException(404, "기업을 찾지 못했습니다")
    edges = query.relations(db, as_of, company_ids=[company_id],
                            rel_types=["equity", "supply_contract", *query.EVENT_TYPES])
    members = query.group_members(db, company_id, as_of)
    order = lambda e: (e["type"], e["subject_id"] != company_id, -(e["value"] or 0))
    return {"company": company_json(found), "as_of": as_of, "business": business_json(db, company_id, as_of),
            "products": products_json(db, company_id, as_of),
            "relations": [edge_json(db, e) for e in sorted(edges, key=order)],
            "group": {"count": len(members),
                      "source": edge_json(db, members[0])["evidence"] if members else [],
                      "members": sorted({m["object_name_raw"] for m in members})[:200]}}


# ---------- 한눈에 보는 칸: 최근 공시, 월별 공시 건수 ----------

_insight_cache: dict = {}
FEED_TYPES = ("supply_contract", "supply_termination", "stake_acquisition", "stake_disposal")


@app.get("/api/hot")
def hot(day: date | None = None, db=Depends(get_db)):
    """그날 함께 오른 종목군 (hot.py 가 계산해 둔 것). day 가 없으면 가장 최근 날. days 는 날짜를 고르는 띠에 쓴다."""
    rows = list(db.scalars(select(HotDay).order_by(HotDay.trade_date.desc()).limit(260)))
    if not rows:
        raise HTTPException(404, "아직 계산한 날이 없습니다")
    row = next((r for r in rows if r.trade_date == day), rows[0])
    return {**row.payload, "days": [{"day": r.trade_date.isoformat(), "market": r.payload["market"],
                                     "up": sum(1 for g in r.payload["groups"] if g["grade"] == "뚜렷함"),
                                     "down": sum(1 for g in r.payload.get("down", {}).get("groups", []) if g["grade"] == "뚜렷함")} for r in rows]}


@app.get("/api/hot/graph")
def hot_graph(day: date, group: int = Query(ge=0), side: str = "up", db=Depends(get_db)):
    """종목군 하나를 그래프로: 그 기업들, 그 사이의 관계 선, 묶어 준 제품 점. side 는 up(오른 쪽) 또는 down(내린 쪽)."""
    row = db.get(HotDay, day)
    groups = [] if row is None else row.payload["groups"] if side == "up" else row.payload.get("down", {}).get("groups", [])
    if group >= len(groups):
        raise HTTPException(404, "그런 종목군이 없습니다")
    found = groups[group]
    ids = {member["id"] for member in found["members"]}
    edges = [e for e in query.relations(db, day, company_ids=ids, rel_types=["equity", "affiliate", "supply_contract"])
             if e["subject_id"] in ids and e["object_id"] in ids]
    graph = build_graph(db, edges, focus=ids)
    index = product_index(db, day)
    for name in found["why"]:
        holders = {i: index[i][name] for i in ids if name in index.get(i, {})}
        if not holders:   # 관계 이름(계열, 지분)이거나 그날의 제품 표에 없다
            continue
        family = next((item["family"] for item in holders.values() if item["family"]), None)
        graph["nodes"].append(_point(product_id(name), name, "product", found["of"], family or "제품", focus=True))
        graph["links"] += [{"source": i, "target": product_id(name), "type": "product", "count": 1, "value": round(item["share"], 2),
                            "label": NO_SHARE if item["share"] <= 0 else f"매출의 {'약 ' if item['split'] else ''}{item['share']:.1f}%"}
                           for i, item in holders.items()]
    return graph


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
                       "subject_id": subject.company_id, "subject": subject.label,
                       "object_id": obj and obj.company_id, "object": obj.label if obj else r.object_name_raw,
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
                        "company_id": filer.company_id, "company": filer.label,
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


class Said(BaseModel):
    question: str = Field(max_length=300)
    answer: str = Field(max_length=6000)


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    # 같은 대화의 앞선 질문과 답 (글만). "그중 가장 큰 곳은?" 같은 이어지는 질문을 알아듣는 데 쓴다
    history: list[Said] = Field(default_factory=list, max_length=4)


def _history(body: Ask) -> list[tuple[str, str]]:
    return [(said.question, said.answer) for said in body.history if said.question.strip() and said.answer.strip()]


def _visitor(request: Request) -> str:
    address = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    return hashlib.sha256((address + os.environ.get("ASK_SALT", "company-graph")).encode()).hexdigest()[:16]


OWNER = "owner"   # 운영자가 물은 것은 이 이름으로 적어 두고, 방문자들의 하루 한도에서 뺀다


def _is_owner(request: Request) -> bool:
    """운영자인가. 운영자에게는 횟수 한도를 적용하지 않는다. 배포된 서버에서는 ASK_OWNER_KEY 와 같은 값을 X-Owner-Key 로 보내야 한다."""
    # 내 컴퓨터에서 띄운 서버에 내 컴퓨터로 들어온 요청은 운영자다 (배포된 서버에는 이런 요청이 없다)
    local = (not os.environ.get("VERCEL") and "x-forwarded-for" not in request.headers
             and request.client is not None and request.client.host in ("127.0.0.1", "::1"))
    key = os.environ.get("ASK_OWNER_KEY", "")
    return local or (len(key) >= 16 and hmac.compare_digest(request.headers.get("x-owner-key", "").encode(), key.encode()))


def _asked_today(db, visitor: str | None = None) -> int:
    since = datetime.now() - timedelta(hours=24)
    conditions = [AskLog.asked_at >= since, AskLog.visitor == visitor if visitor else AskLog.visitor != OWNER]
    return db.scalar(select(func.count()).select_from(AskLog).where(*conditions)) or 0


@app.get("/api/ask/status")
def ask_status(request: Request, db=Depends(get_db)):
    """질문을 받을 수 있는 상태인지와 남은 횟수."""
    _prepare_ask()
    enabled = bool(os.environ.get("ANTHROPIC_API_KEY")) or os.name == "nt"
    if _is_owner(request):
        return {"enabled": enabled, "model": ASK_MODEL, "left_today": None, "left_for_you": None, "owner": True}
    return {"enabled": enabled, "model": ASK_MODEL,
            "left_today": max(0, ASK_DAILY_LIMIT - _asked_today(db)),
            "left_for_you": max(0, ASK_VISITOR_LIMIT - _asked_today(db, _visitor(request)))}


def _prepare_ask():
    global _ask_ready
    if not _ask_ready:
        Base.metadata.create_all(_engine, tables=[AskLog.__table__])
        _ask_ready = True


NO_SHARE = "매출 비중은 보고서에 없음"
GRAPH_COMPANIES = 500  # 질문 결과 그래프에 그리는 기업 수의 상한
AROUND_COMPANIES = 24   # 한 기업만 물었을 때 함께 그리는 상대의 수


_listed_names: dict[int, str] = {}


def listed_names(db) -> dict[int, set[str]]:
    """상장사 번호 → 비교하기 좋게 다듬은 이름. 답의 글에 어느 회사가 나왔는지 찾는 데 쓴다."""
    if not _listed_names:
        from .names import normalize
        _listed_names.update({c.company_id: {normalize(c.name), normalize(c.label)}
                              for c in db.scalars(select(Company).where(Company.corp_cls.in_(LISTED)))})
    return _listed_names


class _Found:
    """Agent가 도구로 조회한 기업과 관계를 모아, 화면이 그릴 그래프로 만든다.

    답의 글은 다 적지 못해 일부만 적으므로 그래프가 전체를 보여 준다. 제품 표에서 찾은 회사는 모두 그리고,
    답에 이름이 나온 회사는 반드시 그래프에 있고 크게 보이게 한다.
    """

    def __init__(self):
        self.companies: set[int] = set()
        self.focus: set[int] = set()
        self.links: dict[tuple, dict] = {}
        self.products: dict[str, dict[int, float]] = {}   # 제품이나 제품군 → {기업: 매출 비중(%)}
        self.families: set[str] = set()                   # products 의 이름 가운데 제품군인 것
        self.topics: dict[str, dict[int, int]] = {}       # 사업 낱말 → {기업: 보고서에 나온 횟수}
        self.ranked: list[int] = []                       # 찾은 순서대로의 기업. 상한에 걸리면 앞에서부터 그린다
        self.calls = 0                                    # 도구를 부른 횟수. 0이면 조회 없이 답한 것(거절 등)이라 그릴 것이 없다
        self.shown: dict | None = None                    # Agent가 show_on_graph 로 정한 것: {"highlight": [...], "also": [...]}. 있으면 어림 대신 이것을 따른다
        self.receipts: set[str] = set()                   # get_filings 로 읽은 공시. 그 공시의 상대 기업을 선으로 잇는다

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
        self.calls += 1
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
        elif name == "show_on_graph":   # agent_tools.SCREEN_TOOL
            self.calls -= 1   # 조회가 아니다
            also = [int(i) for i in arguments.get("also") or []][:40]
            self.shown = {"highlight": [c["company_id"] for c in result["highlight"]], "also": also}
            self.companies.update(self.shown["highlight"] + also)
        elif name == "get_price_moves":
            groups = result.get("groups", []) + [g for day in result.get("days", []) for g in day["groups"]]
            for group in groups[:8]:   # 종목군의 종목을 그리고, 그 사이의 관계는 _around 가 잇는다
                ids = [m["company_id"] for m in group["members"]]
                self.companies.update(ids)
                self.ranked += [i for i in ids if i not in self.ranked]
            if "company" in result:
                self.focus.add(result["company"]["company_id"])
        elif name == "find_disclosers":
            target = arguments.get("counterparty_id")
            for company in result["companies"][:80]:
                self.companies.add(company["company_id"])
                if target:
                    self.focus.add(target)
                    self.links.setdefault((company["company_id"], target, arguments["rel_type"]),
                                          {"count": company["new"] + company["corrections"], "value": None, "unit": None, "seen": set()})
        elif name == "find_by_product":
            # Agent에게는 비중이 큰 50곳만 갔지만 그래프는 찾은 회사를 모두 그린다. 표준 이름에 걸린 것이 없으면 찾은 낱말을 점으로 쓴다
            for company_id, share, labels in result["_graph"]:
                self.ranked.append(company_id)
                for label, kind in labels:
                    if kind == "family":
                        self.families.add(label)
                    shares = self.products.setdefault(label, {})
                    shares[company_id] = max(shares.get(company_id, 0.0), share)
        elif name == "search_business":
            for company_id, matched in result["_graph"]:
                self.ranked.append(company_id)
                for word, count in matched:
                    self.topics.setdefault(word, {})[company_id] = count
        elif name in ("get_business", "get_products", "get_filings"):
            company_id = result["company"]["company_id"]
            self.focus.add(company_id)
            if name == "get_filings":
                self.receipts.update(f["rcept_no"] for f in result.get("filings", []) if f.get("contents"))
            # 제품 표를 읽었으면 그 회사의 제품을 점으로 그린다 (한 줄에 여럿이면 줄의 비중을 나눈다. 비중이 큰 8개까지)
            rows = sorted(result.get("products") or [], key=lambda row: -(row["share_pct"] or 0)) if name == "get_products" else []
            shown = 0
            for row in rows:
                for product in row["products"] if shown < 8 else []:
                    shares = self.products.setdefault(product["name"], {})
                    shares[company_id] = max(shares.get(company_id, 0.0), round((row["share_pct"] or 0) / len(row["products"]), 2))
                    shown += 1
        elif name == "list_companies":
            self.companies.update(c["company_id"] for c in result["companies"][:80])

    def _link(self, edge: dict):
        """원장에서 읽은 관계 한 줄(query.relations 의 모양)을 선으로 더한다."""
        subject, obj = edge["subject_id"], edge["object_id"]
        if not subject or not obj or subject == obj:
            return
        self.companies.update((subject, obj))
        link = self.links.setdefault((subject, obj, edge["type"]), {"count": 0, "value": None, "unit": edge["unit"], "seen": set()})
        receipt = (edge["rcept_no"],)
        if receipt in link["seen"] or any(edge["rcept_no"] in seen for seen in link["seen"]):
            return
        link["seen"].add(receipt)
        link["count"] += 1
        if edge["value"] is not None:
            link["value"] = float(edge["value"]) if edge["type"] == "equity" else (link["value"] or 0) + float(edge["value"])

    def _contracts(self, db):
        """get_filings 로 읽은 공시의 상대 기업을 잇는다. 도구 결과에는 상대의 이름만 있어서 원장에서 다시 찾는다."""
        if not self.receipts:
            return
        receipts = list(self.receipts)[:200]
        for edge in query.relations(db, date.today(), company_ids=list(self.focus), rel_types=["supply_contract", *query.EVENT_TYPES]):
            if edge["rcept_no"] in receipts and edge["type"] not in ("equity", "affiliate"):
                self._link(edge)

    def _around(self, db):
        """기업 한두 곳만 묻고 관계는 조회하지 않은 질문("○○ 근황 어때")이면 점 하나만 남는다.
        그럴 때는 그 기업의 지분과 공급계약 가운데 큰 것을 함께 그려, 그 기업이 어디에 놓여 있는지 보이게 한다."""
        # 제품 점에라도 이어져 있으면 혼자가 아니다 (제품 비중을 비교하는 질문에 계약 상대를 수십 곳 덧그리지 않는다)
        linked = {i for source, target, _ in self.links for i in (source, target)}
        linked |= {i for members in (*self.products.values(), *self.topics.values()) for i in members}
        alone = [i for i in self.focus if i not in linked]
        if not alone or len(self.focus) > 3:
            return
        for company_id in alone:
            edges = query.relations(db, date.today(), company_ids=[company_id], rel_types=["equity", "supply_contract"])
            edges = [e for e in edges if e["subject_id"] and e["object_id"]]
            other = lambda e: e["object_id"] if e["subject_id"] == company_id else e["subject_id"]
            seen = set()
            for edge in sorted(edges, key=lambda e: -(float(e["value"]) if e["value"] is not None else 0)):
                if len(seen) >= AROUND_COMPANIES and other(edge) not in seen:
                    continue
                seen.add(other(edge))
                self._link(edge)

    def graph(self, db, answer: str = "") -> dict:
        from .names import normalize

        if self.calls == 0:   # 조회 없이 답했다(매수·매도 판단을 거절한 경우 등). 그릴 것이 없다
            return {"nodes": [], "links": []}
        self._contracts(db)
        self._around(db)
        products = {name: dict(members) for name, members in self.products.items()}
        topics = {word: dict(members) for word, members in self.topics.items()}

        # 답에 이름이 나온 회사는 크게 보인다. 조회 결과에 있던 회사만 본다 ("뉴트리션"이라는 낱말에서 뉴트리를 찾지 않게).
        # 긴 이름부터 찾고 찾은 자리는 지운다 ("SK하이닉스"에서 이닉스를, "현대모비스"에서 모비스를 또 찾지 않게)
        candidates = self.companies | self.focus | set(self.ranked)
        names = [(name, company_id) for company_id, known in listed_names(db).items() for name in known]
        unlisted = candidates - listed_names(db).keys()
        if unlisted:
            names += [(name, c.company_id) for c in db.scalars(select(Company).where(Company.company_id.in_(unlisted)))
                      for name in {normalize(c.name), normalize(c.label)}]
        text, mentioned = normalize(answer), set()
        for name, company_id in sorted(set(names), key=lambda pair: -len(pair[0])):
            if len(name) >= 2 and name in text:
                text = text.replace(name, "\0" * len(name))
                if company_id in candidates:
                    mentioned.add(company_id)
        if self.shown:   # Agent가 정했다: 강조는 그 기업들만, 그릴 것은 강조와 함께 그릴 기업만
            focus = set(self.shown["highlight"])
            ids = focus | set(self.shown["also"])
        else:
            focus = self.focus | mentioned
            ids = set(focus)
            for company_id in [*self.ranked, *sorted(self.companies)]:
                if len(ids) >= GRAPH_COMPANIES:
                    break
                ids.add(company_id)

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

        # 3. 답에 나왔는데 어디에도 이어지지 않은 회사는 자기 제품에 잇는다 (매출 비중이 큰 둘)
        linked = set(degree) | {i for members in (*products.values(), *topics.values()) for i in members}
        own = product_index(db, date.today())
        for company_id in mentioned - linked:
            for name, item in sorted(own.get(company_id, {}).items(), key=lambda x: -x[1]["share"])[:2]:
                products.setdefault(name, {})[company_id] = round(item["share"], 2)

        # 4. 제품 점과 낱말 점. 이름이 같으면 제품 점 하나로 합친다
        extra = []
        for name, members in products.items():
            members = {i: share for i, share in members.items() if i in ids}
            mentions = {i: n for i, n in topics.pop(name, {}).items() if i in ids and i not in members}
            if not members and not mentions:
                continue
            # 제품군으로 찾은 것은 제품군 점, 낱말로 찾은 것은 제품 점
            is_family = name in self.families
            number = family_id(name) if is_family else product_id(name)
            extra.append(_point(number, name, "family" if is_family else "product", len(members) + len(mentions),
                                FAMILY_FIELD.get(name, "제품군") if is_family else "제품", focus=True))
            for company_id, share in members.items():
                links.append({"source": company_id, "target": number, "type": "product", "count": 1, "value": share,
                              "label": NO_SHARE if share <= 0 else f"매출의 {share:.1f}%"})
                degree[company_id] += 1
            for company_id, count in mentions.items():
                links.append({"source": company_id, "target": number, "type": "business", "count": count, "value": None,
                              "label": f"보고서의 사업 내용에 '{name}' {count}번"})
                degree[company_id] += 1
        for number, (word, members) in enumerate(topics.items(), 1):
            members = {i: n for i, n in members.items() if i in ids}
            if not members:
                continue
            # 낱말 점의 번호는 음수로 준다. 기업 번호와 겹치지 않게
            extra.append(_point(-number, word, "topic", len(members), "사업 낱말", focus=True))
            for company_id, count in members.items():
                links.append({"source": company_id, "target": -number, "type": "business", "count": count, "value": None,
                              "label": f"보고서의 사업 내용에 '{word}' {count}번"})
                degree[company_id] += 1
        nodes = [{**company_json(c), "degree": degree[c.company_id], "focus": c.company_id in focus,
                  "mentioned": c.company_id in mentioned}
                 for c in db.scalars(select(Company).where(Company.company_id.in_(ids)))] if ids else []
        return {"nodes": nodes + extra, "links": links}


def _admit(body: Ask, request: Request, db) -> tuple[str, str, int]:
    """질문을 받을 수 있는지 보고, 받으면 먼저 센다. (다듬은 질문, 방문자, 기록 번호)"""
    _prepare_ask()
    visitor = OWNER if _is_owner(request) else _visitor(request)
    if visitor != OWNER and _asked_today(db, visitor) >= ASK_VISITOR_LIMIT:
        raise HTTPException(429, f"한 사람이 하루에 물을 수 있는 횟수({ASK_VISITOR_LIMIT}번)를 다 썼습니다. 내일 다시 물어 주세요.")
    if visitor != OWNER and _asked_today(db) >= ASK_DAILY_LIMIT:
        raise HTTPException(429, "오늘 받을 수 있는 질문을 다 받았습니다. 미리 돌려 둔 예시는 '평가' 탭에서 볼 수 있습니다.")
    question = " ".join(body.question.split())
    entry = AskLog(asked_at=datetime.now(), visitor=visitor, question=question[:300], ok=False)
    db.add(entry)
    db.commit()   # 답이 오기 전에 먼저 센다. 동시에 여러 번 눌러도 상한을 넘지 않게
    return question, visitor, entry.ask_id


def _answered(db, question: str, visitor: str, ask_id: int, result: dict, found: _Found) -> dict:
    """Agent의 답을 화면에 줄 모양으로 만든다. 출처와 그래프는 답의 글과 따로, 원장에서 다시 찾아 붙인다."""
    entry = db.get(AskLog, ask_id)
    entry.ok, entry.tokens, entry.seconds = True, sum(result["usage"].values()), result["seconds"]
    db.commit()
    # 출처: 답에 적힌 접수번호 가운데 조회 결과에 실제로 있었던 공시
    cited = [no for no in result["cited"] if no not in result["cited_not_in_results"]]
    documents = {d.rcept_no: d for d in db.scalars(select(Document).where(Document.rcept_no.in_(cited))).all()} if cited else {}
    sources = [{"rcept_no": no, "url": query.DART_VIEWER + no,
                "company": documents[no].company_id and db.get(Company, documents[no].company_id).label,
                "report": documents[no].report_nm.strip(), "filed": documents[no].rcept_dt.isoformat()}
               if no in documents else {"rcept_no": no, "url": query.DART_VIEWER + no, "company": None, "report": None, "filed": None}
               for no in cited]
    return {"question": question, "answer": result["answer"], "followups": result.get("followups", []),
            "as_of": result["as_of"], "model": result["model"],
            "seconds": result["seconds"], "tokens": entry.tokens,
            "tools": [{"name": c["tool"], "input": c["input"], "total": c["total"], "error": c["error"]} for c in result["tool_calls"]],
            "unverified_citations": result["cited_not_in_results"],
            "sources": sources,
            "graph": found.graph(db, result["answer"]),
            "left_for_you": None if visitor == OWNER else max(0, ASK_VISITOR_LIMIT - _asked_today(db, visitor))}


UNAVAILABLE = "지금은 Agent가 답할 수 없습니다. 잠시 뒤에 다시 시도해 주세요."


@app.post("/api/ask")
def ask(body: Ask, request: Request, db=Depends(get_db)):
    """질문 하나를 Agent에게 넘기고, 답과 함께 조회된 기업·관계를 그래프로 돌려준다."""
    from . import agent   # anthropic 패키지는 여기서만 필요하다

    question, visitor, ask_id = _admit(body, request, db)
    found = _Found()
    try:
        result = agent.answer(question, model=ASK_MODEL, as_of=date.today(), max_turns=8, on_result=found.take, history=_history(body))
    except Exception as error:   # 키가 없거나 API가 실패한 경우. 방문자에게는 사정만 알린다
        raise HTTPException(503, UNAVAILABLE) from error
    return _answered(db, question, visitor, ask_id, result, found)


@app.post("/api/ask/stream")
def ask_stream(body: Ask, request: Request, db=Depends(get_db)):
    """ask 와 같되, 답을 만들어지는 대로 흘려보낸다 (Server-Sent Events).

    사건: tool(지금 부르는 도구와 입력), text(답의 글 조각), turn(도구 결과를 받고 새로 쓰기 시작),
    done(ask 가 돌려주는 것과 같은 값), error(사유). 횟수 초과 같은 거절은 흘려보내기 전에 보통의 오류 응답으로 준다.
    """
    from . import agent

    question, visitor, ask_id = _admit(body, request, db)
    events: queue.Queue = queue.Queue()

    history = _history(body)
    def work():
        found = _Found()
        try:
            result = agent.answer(question, model=ASK_MODEL, as_of=date.today(), max_turns=8, on_result=found.take,
                                  history=history, on_event=lambda kind, value: events.put((kind, value)))
            with session(_engine) as own:   # 요청의 DB 연결은 응답을 흘려보내는 동안 닫힐 수 있어서 따로 연다
                events.put(("done", _answered(own, question, visitor, ask_id, result, found)))
        except Exception:
            events.put(("error", UNAVAILABLE))

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            try:
                kind, value = events.get(timeout=10)
            except queue.Empty:
                yield ": 기다리는 중\n\n"   # 사이에 있는 장비가 연결을 끊지 않게
                continue
            yield f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False, default=str)}\n\n"
            if kind in ("done", "error"):
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
