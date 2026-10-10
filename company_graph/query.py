"""조회 계층. Agent와 화면은 relation 표를 직접 읽지 않고 여기를 거친다.

여기서 지키는 규칙 (DESIGN.md 6장):
1. 시점: 날짜 D에 보이는 줄 = 공개일 ≤ D, 그리고 무효일이 없거나 무효일 > D. 우리가 내린 줄(retired_at)은 보지 않는다
2. 지분: 보고서를 낸 회사마다 그 시점에 나와 있는 가장 최근 사업보고서의 표만 쓴다
   (처분한 지분은 새 보고서에 없으므로 사라진다). 그렇게 남은 줄을 (주체, 상대)로 합친다. 두 표에 다 있으면 보유한 회사가 직접 낸 타법인 출자현황을 쓰고,
   다른 쪽 공시는 근거에 함께 남긴다
3. 계열: 지분과 같이 가장 최근 사업보고서의 표만 쓴다
4. 공급계약: 그 시점에 유효한 계약을 전부 (정정 전 줄은 무효일로 걸러진다)
5. 선마다 누가 밝혔는지(disclosed_by)와 오래됐는지(stale)를 붙인다. "없음"과 "모름"을 구분하려면 coverage()를 함께 본다

실행 예: python -m company_graph.query 현대제철 --as-of 2026-02-01
"""
import argparse
from collections import defaultdict, deque
from datetime import date, timedelta

from sqlalchemy import and_, func, or_, select

from .db import Company, CompanyAlias, Document, Relation, session
from .names import clean_reported, normalize

LABELS = {"equity": "지분", "affiliate": "계열", "supply_contract": "공급계약", "supply_termination": "공급계약 해지",
          "major_customer": "주요 고객",
          "stake_acquisition": "지분 취득 결정", "stake_disposal": "지분 처분 결정", "merger": "합병 결정",
          "split": "분할 결정", "business_transfer": "영업양수도 결정"}
EVENT_TYPES = ("supply_termination", "stake_acquisition", "stake_disposal", "merger", "split", "business_transfer")
DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
# 사업보고서는 1년에 한 번 나온다. 기준일이 이보다 오래된 지분·계열은 "그 뒤 보고서가 없다"는 뜻이다
STALE_AFTER = timedelta(days=548)
# 최대주주 현황에만 있는 칸. 가진 쪽의 출자현황 줄을 대표로 쓸 때도 이것은 옮겨 싣는다
HOLDER_KEYS = ("relation_to_filer", "holder_name_raw", "shares_begin", "pct_begin", "note", "table_total")
NOTICE ="공시된 관계만 보여 줍니다. 여기에 없다고 해서 관계가 없다는 뜻은 아닙니다."


def visible(as_of: date):
    return and_(Relation.retired_at.is_(None), Relation.disclosed_date <= as_of,
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


def _rows(db, as_of: date, company_ids, rel_types, direction: str, superseded: bool = False) -> list[Relation]:
    conditions = [and_(Relation.retired_at.is_(None), Relation.disclosed_date <= as_of) if superseded else visible(as_of)]
    if rel_types:
        conditions.append(Relation.rel_type.in_(rel_types))
    if company_ids is not None:
        ids = list(company_ids)
        if direction == "within":   # 양쪽이 다 이 기업들 안에 있는 관계만
            conditions += [Relation.subject_company_id.in_(ids), Relation.object_company_id.in_(ids)]
        else:
            sides = {"out": [Relation.subject_company_id.in_(ids)], "in": [Relation.object_company_id.in_(ids)],
                     "both": [Relation.subject_company_id.in_(ids), Relation.object_company_id.in_(ids)]}[direction]
            conditions.append(or_(*sides))
    return list(db.scalars(select(Relation).where(*conditions)))


def _edge(rel: Relation, evidence: list[str], disclosed_by: str, as_of: date) -> dict:
    stale = rel.rel_type in ("equity", "affiliate") and rel.as_of_date is not None and as_of - rel.as_of_date > STALE_AFTER
    return {"type": rel.rel_type, "subject_id": rel.subject_company_id, "subject_name_raw": rel.subject_name_raw,
            "object_id": rel.object_company_id,
            "disclosed_by": disclosed_by, "stale": stale,
            "object_name_raw": rel.object_name_raw, "value": rel.value_num, "unit": rel.value_unit,
            "as_of_date": rel.as_of_date, "disclosed_date": rel.disclosed_date, "rcept_no": rel.rcept_no,
            "invalidated_date": rel.invalidated_date,
            "evidence": evidence, "trust_tier": rel.trust_tier, "attrs": rel.attrs or {}}


def _filer(rel: Relation) -> int:
    """이 줄이 실린 보고서를 낸 회사. 최대주주 현황에서는 상대가, 나머지 표에서는 주체가 보고서를 냈다."""
    if rel.rel_type == "equity" and (rel.attrs or {}).get("source") == "largest_shareholders":
        return rel.object_company_id
    return rel.subject_company_id


def _latest_reports(db, as_of: date, filer_ids) -> dict[int, str]:
    """회사마다 그 시점에 나와 있는 가장 최근 사업보고서의 접수번호."""
    latest: dict[int, tuple] = {}
    for company_id, rcept_no, year in db.execute(
            select(Document.company_id, Document.rcept_no, Document.bsns_year).where(
                Document.doc_type == "annual", Document.rcept_dt <= as_of, Document.company_id.in_(list(filer_ids)))):
        latest[company_id] = max(latest.get(company_id, (0, "")), (year or 0, rcept_no))
    return {company_id: rcept_no for company_id, (_, rcept_no) in latest.items()}


def _side(rel: Relation) -> str:
    """이 줄을 밝힌 쪽. 주체가 낸 공시면 subject, 상대가 낸 공시면 object."""
    return "object" if _filer(rel) == rel.object_company_id and rel.object_company_id != rel.subject_company_id else "subject"


def _reduce(rows: list[Relation], latest_report: dict[int, str], as_of: date, exited: bool = False) -> list[dict]:
    """위 규칙 2~4를 적용해 줄을 선으로 줄인다."""
    edges = []
    current = [r for r in rows if r.rel_type not in ("equity", "affiliate") or latest_report.get(_filer(r)) == r.rcept_no]
    if not exited:   # 기간 중에 주식을 다 내놓은 주주의 0% 줄은 주주 명단의 변동을 물을 때만 쓴다
        current = [r for r in current if not (r.attrs or {}).get("exited")]

    equity = defaultdict(list)
    for r in current:
        if r.rel_type == "equity":
            equity[(r.subject_company_id or r.subject_name_raw, r.object_company_id or r.object_name_raw)].append(r)
    for group in equity.values():
        latest = max(r.as_of_date for r in group)
        same_date = sorted((r for r in group if r.as_of_date == latest),
                           key=lambda r: (r.attrs or {}).get("source") != "other_corp_investments")
        sides = {_side(r) for r in same_date}
        edge = _edge(same_date[0], sorted({r.rcept_no for r in same_date}),
                     "both" if len(sides) == 2 else sides.pop(), as_of)
        # 가진 쪽의 표를 대표로 쓰더라도, 내준 쪽 표에 적힌 관계(최대주주 본인, 계열회사 등)는 같이 싣는다
        listed = next(((r.attrs or {}) for r in same_date if (r.attrs or {}).get("source") == "largest_shareholders"), {})
        edge["attrs"] = {**edge["attrs"], **{k: listed[k] for k in HOLDER_KEYS if listed.get(k) not in (None, "")}}
        if len(sides) == 2:
            # 같은 지분을 두 회사가 저마다 적었다. 값이 서로 다를 수 있어 양쪽 것을 다 남긴다
            edge["reports"] = [{"side": _side(r), "source": (r.attrs or {}).get("source"), "value": r.value_num,
                                "shares": (r.attrs or {}).get("shares"), "rcept_no": r.rcept_no} for r in same_date]
            edge["differs"] = len({r.value_num for r in same_date}) > 1
        edges.append(edge)

    edges += [_edge(r, [r.rcept_no], "subject", as_of) for r in current if r.rel_type != "equity"]
    return edges


def relations(db, as_of: date, *, company_ids=None, rel_types=None, direction: str = "both",
              include_superseded: bool = False, include_exited: bool = False) -> list[dict]:
    """그 시점에 보이는 관계. company_ids를 주면 그 기업이 주체이거나 상대인 것만.
    include_superseded 면 그 뒤 정정·해지·철회로 무효가 된 공시도 넣는다 ("그 달에 나온 공시를 모두"에 답할 때)."""
    rows = _rows(db, as_of, company_ids, rel_types, direction, include_superseded)
    filers = {_filer(r) for r in rows if r.rel_type in ("equity", "affiliate")}
    return _reduce(rows, _latest_reports(db, as_of, filers) if filers else {}, as_of, include_exited)


def coverage(db) -> dict:
    """무엇을 얼마나 모았는지. 빈 결과가 "관계 없음"인지 "모으지 않음"인지 구분하는 데 쓴다."""
    kinds = {}
    for rel_type, first, last, count in db.execute(
            select(Relation.rel_type, func.min(Relation.disclosed_date), func.max(Relation.disclosed_date), func.count())
            .where(Relation.retired_at.is_(None)).group_by(Relation.rel_type)):
        kinds[rel_type] = {"label": LABELS[rel_type], "first_disclosed": first, "last_disclosed": last, "rows": count}
    return {"notice": NOTICE, "relations": kinds,
            "sources": {"equity": "사업보고서의 타법인 출자현황과 최대주주 현황 (반기·분기보고서는 아직 없음). 주주 쪽은 최대주주와 그 특수관계인만 있고, 5% 이상 주주 전체가 아니다. 개인·정부 같은 원장에 없는 주주는 이름만 있다. 지분율은 그 표에 적힌 값으로, 회사에 따라 우선주를 포함한 발행주식총수 기준이다. 같은 보고서의 5% 이상 주주 표(보통주나 의결권 있는 주식 기준)의 값과 다를 수 있다",
                        "affiliate": "사업보고서의 계열회사 현황 표",
                        "supply_contract": "단일판매ㆍ공급계약 체결 공시 (건별로 공시한 계약만)",
                        "supply_termination": "단일판매ㆍ공급계약 해지 공시. 해지된 계약은 공급계약 조회에서 빠진다",
                        "stake_acquisition": "타법인 주식 및 출자증권 취득결정 공시 (2024-01 이후, 전 시장). 철회된 결정은 빠진다",
                        "stake_disposal": "타법인 주식 및 출자증권 처분결정 공시 (2024-01 이후, 전 시장). 철회된 결정은 빠진다",
                        "group": "기업의 group 값은 공정거래위원회가 2026년 5월에 지정한 대규모기업집단의 소속회사 명단에서 왔다. "
                                 "group 이 없는 기업은 그 명단에 없다, 곧 지정 집단 소속이 아니다. 계열회사 표는 지정 여부와 무관하게 회사가 사업보고서에 적은 것이다"}}


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
        if e["object_id"] and e["subject_id"]:
            links[(e["subject_id"], e["object_id"], e["type"])].append(e)
    names = dict(db.execute(select(Company.company_id, func.coalesce(Company.display_name, Company.name)).where(Company.company_id.in_(seen))).all())
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
    name = lambda i: db.get(Company, i).label if i else None
    value = ""
    if edge["value"] is not None:
        value = f" {edge['value']:.2f}%" if edge["unit"] == "pct" else f" {int(edge['value']):,}원"
    title = edge["attrs"].get("title")
    return (f"{name(edge['subject_id']) or edge['subject_name_raw'] + ' (원장에 없음)'} → {name(edge['object_id']) or edge['object_name_raw'] + ' (원장에 없음)'}"
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
        print(f"{company.label} ({company.stock_code or '비상장'}) — {args.as_of} 시점")
        edges = relations(db, args.as_of, company_ids=[company.company_id], rel_types=args.type)
        for edge in sorted(edges, key=lambda e: (e["type"], e["subject_id"] != company.company_id, -(e["value"] or 0))):
            if edge["object_id"] or not args.linked_only:
                print(" ", describe(db, edge))
        print(f"{len(edges)}줄")


if __name__ == "__main__":
    main()
