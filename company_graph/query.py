"""조회 계층. Agent와 화면은 relation 표를 직접 읽지 않고 여기를 거친다.

여기서 지키는 규칙 (DESIGN.md 6장):
1. 시점: 날짜 D에 보이는 줄 = 공개일 ≤ D, 그리고 무효일이 없거나 무효일 > D
2. 지분: (주체, 상대)마다 그 시점에 보이는 가장 최근 기준일의 값 하나. 같은 기준일이 두 표에서 왔으면
   보유한 회사가 직접 낸 타법인 출자현황을 쓰고, 다른 쪽 공시는 근거에 함께 남긴다
3. 계열: 보고서를 낸 회사마다 그 시점에 보이는 가장 최근 보고서의 표만 쓴다
4. 공급계약: 그 시점에 유효한 계약을 전부 (정정 전 줄은 무효일로 걸러진다)

실행 예: python -m company_graph.query 현대제철 --as-of 2026-02-01
"""
import argparse
from collections import defaultdict, deque
from datetime import date

from sqlalchemy import and_, or_, select

from .db import Company, CompanyAlias, Relation, session
from .names import clean_reported, normalize

LABELS = {"equity": "지분", "affiliate": "계열", "supply_contract": "공급계약", "major_customer": "주요 고객"}
DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


def visible(as_of: date):
    return and_(Relation.disclosed_date <= as_of,
                or_(Relation.invalidated_date.is_(None), Relation.invalidated_date > as_of))


def find_companies(db, text: str, limit: int = 10) -> list[Company]:
    """이름이나 종목코드로 기업을 찾는다. 정확히 맞는 것이 있으면 그것만 돌려준다."""
    if text.isdigit() and len(text) == 6:
        return list(db.scalars(select(Company).where(Company.stock_code == text)))
    key = normalize(clean_reported(text))
    exact = list(db.scalars(select(Company).join(CompanyAlias).where(CompanyAlias.alias == key).distinct()))
    if exact:
        # 지금 상장된 회사를 앞에 둔다 (상장폐지된 옛 회사와 이름이 겹칠 수 있다)
        return sorted(exact, key=lambda c: (c.corp_cls not in ("Y", "K"), not c.in_scope, c.company_id))
    return list(db.scalars(select(Company).join(CompanyAlias).where(CompanyAlias.alias.like(f"%{key}%"))
                           .distinct().limit(limit)))


def _rows(db, as_of: date, company_ids, rel_types, direction: str) -> list[Relation]:
    conditions = [visible(as_of)]
    if rel_types:
        conditions.append(Relation.rel_type.in_(rel_types))
    if company_ids is not None:
        ids = list(company_ids)
        sides = {"out": [Relation.subject_company_id.in_(ids)], "in": [Relation.object_company_id.in_(ids)],
                 "both": [Relation.subject_company_id.in_(ids), Relation.object_company_id.in_(ids)]}[direction]
        conditions.append(or_(*sides))
    return list(db.scalars(select(Relation).where(*conditions)))


def _edge(rel: Relation, evidence: list[str]) -> dict:
    return {"type": rel.rel_type, "subject_id": rel.subject_company_id, "object_id": rel.object_company_id,
            "object_name_raw": rel.object_name_raw, "value": rel.value_num, "unit": rel.value_unit,
            "as_of_date": rel.as_of_date, "disclosed_date": rel.disclosed_date, "rcept_no": rel.rcept_no,
            "evidence": evidence, "trust_tier": rel.trust_tier, "attrs": rel.attrs or {}}


def _reduce(rows: list[Relation]) -> list[dict]:
    """위 규칙 2~4를 적용해 줄을 선으로 줄인다."""
    edges = []

    equity = defaultdict(list)
    for r in rows:
        if r.rel_type == "equity":
            equity[(r.subject_company_id, r.object_company_id or r.object_name_raw)].append(r)
    for group in equity.values():
        latest = max(r.as_of_date for r in group)
        same_date = sorted((r for r in group if r.as_of_date == latest),
                           key=lambda r: (r.attrs or {}).get("source") != "other_corp_investments")
        edges.append(_edge(same_date[0], sorted({r.rcept_no for r in same_date})))

    by_filer = defaultdict(list)
    for r in rows:
        if r.rel_type == "affiliate":
            by_filer[r.subject_company_id].append(r)
    for group in by_filer.values():
        latest = max((r.as_of_date or r.disclosed_date, r.rcept_no) for r in group)[1]
        edges += [_edge(r, [r.rcept_no]) for r in group if r.rcept_no == latest]

    edges += [_edge(r, [r.rcept_no]) for r in rows if r.rel_type not in ("equity", "affiliate")]
    return edges


def relations(db, as_of: date, *, company_ids=None, rel_types=None, direction: str = "both") -> list[dict]:
    """그 시점에 보이는 관계. company_ids를 주면 그 기업이 주체이거나 상대인 것만."""
    rows = _rows(db, as_of, company_ids, rel_types, direction)
    if company_ids is not None and (not rel_types or "affiliate" in rel_types) and direction != "out":
        # 계열은 보고서 단위로 최신본을 골라야 하므로, 상대로 걸린 줄의 보고서 전체를 다시 본다
        filers = {r.subject_company_id for r in rows if r.rel_type == "affiliate"} - set(company_ids)
        if filers:
            wanted = set(company_ids)
            extra = _rows(db, as_of, filers, ["affiliate"], "out")
            kept = [e for e in _reduce(extra) if e["object_id"] in wanted]
            rows = [r for r in rows if not (r.rel_type == "affiliate" and r.subject_company_id in filers)]
            return _reduce(rows) + kept
    return _reduce(rows)


def group_members(db, company_id: int, as_of: date) -> list[dict]:
    """이 기업과 같은 집단에 속한 회사. 직접 낸 보고서가 없으면 이 기업을 계열로 적은 다른 회사의 표를 쓴다."""
    own = [e for e in relations(db, as_of, company_ids=[company_id], rel_types=["affiliate"], direction="out")]
    if own:
        return own
    listed_by = relations(db, as_of, company_ids=[company_id], rel_types=["affiliate"], direction="in")
    if not listed_by:
        return []
    filer = max(listed_by, key=lambda e: (e["as_of_date"] or e["disclosed_date"], e["rcept_no"]))["subject_id"]
    return relations(db, as_of, company_ids=[filer], rel_types=["affiliate"], direction="out")


def neighborhood(db, center_ids, as_of: date, *, rel_types=None, hops: int = 1) -> dict:
    """화면용: 중심 기업에서 hops 단계까지의 점과 선. 같은 두 기업 사이의 같은 종류 관계는 선 하나로 묶는다."""
    seen, frontier, edges = set(center_ids), set(center_ids), []
    for _ in range(hops):
        found = relations(db, as_of, company_ids=frontier, rel_types=rel_types)
        edges += found
        frontier = {i for e in found for i in (e["subject_id"], e["object_id"]) if i} - seen
        seen |= frontier
        if not frontier:
            break
    links = defaultdict(list)
    for e in edges:
        if e["object_id"]:
            links[(e["subject_id"], e["object_id"], e["type"])].append(e)
    names = dict(db.execute(select(Company.company_id, Company.name).where(Company.company_id.in_(seen))).all())
    return {"nodes": [{"id": i, "name": names.get(i, "?")} for i in sorted(seen)],
            "links": [{"source": s, "target": o, "type": t, "count": len({e["rcept_no"] for e in es}),
                       "evidence": sorted({r for e in es for r in e["evidence"]})}
                      for (s, o, t), es in links.items()]}


def paths(db, start_id: int, goal_id: int, as_of: date, *, rel_types=None, max_hops: int = 3) -> list[list[dict]]:
    """두 기업을 잇는 가장 짧은 경로들. 방향은 따지지 않는다."""
    previous: dict[int, list[tuple[int, dict]]] = {start_id: []}
    depth, queue = {start_id: 0}, deque([start_id])
    while queue:
        node = queue.popleft()
        if node == goal_id or depth[node] >= max_hops:
            continue
        for e in relations(db, as_of, company_ids=[node], rel_types=rel_types):
            other = e["object_id"] if e["subject_id"] == node else e["subject_id"]
            if other is None:
                continue
            if other not in depth:
                depth[other], previous[other] = depth[node] + 1, []
                queue.append(other)
            if depth[other] == depth[node] + 1:
                previous[other].append((node, e))

    def walk(node: int) -> list[list[dict]]:
        if node == start_id:
            return [[]]
        return [route + [edge] for parent, edge in previous.get(node, []) for route in walk(parent)]

    unique = {tuple((e["subject_id"], e["object_id"], e["type"]) for e in route): route
              for route in (walk(goal_id) if goal_id in depth else [])}
    return list(unique.values())


def describe(db, edge: dict) -> str:
    name = lambda i: db.get(Company, i).name if i else None
    value = ""
    if edge["value"] is not None:
        value = f" {edge['value']:.2f}%" if edge["unit"] == "pct" else f" {int(edge['value']):,}원"
    title = edge["attrs"].get("title")
    return (f"{name(edge['subject_id'])} → {name(edge['object_id']) or edge['object_name_raw'] + ' (원장에 없음)'}"
            f" | {LABELS[edge['type']]}{value}{' | ' + title if title else ''}"
            f" | 기준일 {edge['as_of_date']} | 공개일 {edge['disclosed_date']} | 근거 {', '.join(edge['evidence'])}")


def main():
    parser = argparse.ArgumentParser(description="한 기업의 관계를 시점 기준으로 조회한다")
    parser.add_argument("company")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--type", choices=list(LABELS), action="append")
    parser.add_argument("--linked-only", action="store_true", help="상대가 원장에 있는 것만")
    args = parser.parse_args()
    with session() as db:
        found = find_companies(db, args.company)
        if not found:
            print("기업을 찾지 못했습니다")
            return
        company = found[0]
        print(f"{company.name} ({company.stock_code or '비상장'}) — {args.as_of} 시점")
        edges = relations(db, args.as_of, company_ids=[company.company_id], rel_types=args.type)
        for edge in sorted(edges, key=lambda e: (e["type"], e["subject_id"] != company.company_id, -(e["value"] or 0))):
            if edge["object_id"] or not args.linked_only:
                print(" ", describe(db, edge))
        print(f"{len(edges)}줄")


if __name__ == "__main__":
    main()
