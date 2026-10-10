"""그날 함께 오른 무리 찾기. 테마를 미리 묶어 두지 않고, 오른 종목에서 거꾸로 "무엇으로 이어져 있나"를 찾는다.

이어 주는 것은 둘이다.
- 제품: 몇 곳만 가진 제품 이름 하나(면역진단 키트, 강관). 그것을 가진 상장사 가운데 여럿이 함께 올랐다
- 관계: 지분·계열·공급계약으로 직접 이어진 상장사들이 함께 올랐다(그룹주)

규칙은 2026-01-02 ~ 10-07 의 시세를 보며 정했다. 그 기간의 숫자는 규칙을 고른 데이터에서 나온 것이므로,
규칙이 맞는지는 `check` 로 다른 기간에 돌려 등락률을 섞은 결과와 견주어 본다.

실행: python -m company_graph.hot store                      아직 계산하지 않은 날을 계산해 hot_day 에 넣는다 (store 시작 끝 으로 기간을 정할 수도 있다)
      python -m company_graph.hot 2026-10-07                 그날의 무리를 찍어 본다
      python -m company_graph.hot check 2025-02-01 2025-12-30   기간 전체를 돌려 섞은 결과와 견줌 (--down 이면 내린 쪽, --save=파일.json)
"""
import collections
import json
import random
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select

from .db import Company, Document, HotDay, PriceDaily, Product, Relation, session

LIQUID_DAYS, LIQUID_VALUE = 20, 1e8   # 최근 20거래일 평균 거래대금이 1억 원은 되어야 본다 (그 가운데 15일은 거래가 있어야 한다)
FLOOR, TOP = 3.0, 0.08                # 오른 곳: 시장 중앙값보다 3%p 이상이면서 그날 상위 8%
NARROW = 12                           # 이보다 많은 회사가 가진 제품 이름은 무리의 이유로 쓰지 않는다 ("화장품" 같은 넓은 이름)
MIN_SHARE = 10                        # 그 제품이 매출의 10%는 되어야 가진 것으로 본다
HUB = 10                              # 상장사끼리의 선이 이보다 많은 회사(여러 곳에 출자한 투자회사)를 거쳐서는 잇지 않는다
STRONG = 5.0                          # 무리에서 셋째로 많이 오른 곳이 시장보다 5%p는 올라야 한다. 한 곳만 급등한 무리를 막는다
SOLO = 10.0                           # 무리에 들지 못하고 이만큼 오른 곳은 "혼자 오른 곳"으로 따로 둔다
ALONE_SHOWN = 20                      # 혼자 오른 곳은 많이 오른 순으로 이만큼만 남긴다
FILING_DAYS = 4                       # 혼자 오른 곳에 붙이는 공시: 그날까지 나흘 사이에 나온 것
MARKET_NAMES = {"Y": "코스피", "K": "코스닥", "N": "코넥스"}   # DART 의 법인 구분. 유가증권시장을 흔히 부르는 대로 코스피라고 적는다
REL_NAMES = {"affiliate": "계열", "equity": "지분", "supply_contract": "공급계약", "supply_termination": "공급계약 해지",
             "major_customer": "주요 고객", "stake_acquisition": "지분 취득 결정", "stake_disposal": "지분 처분 결정",
             "merger": "합병 결정", "split": "분할 결정", "business_transfer": "영업양수도 결정"}
CLEAR, FAIR = "뚜렷함", "보통"


@dataclass
class Links:
    """상장사들을 이어 주는 것. 날마다 바뀌지 않으므로 한 번 읽어 여러 날에 쓴다."""
    names: dict[int, str]
    holders: dict[str, set[int]]                    # 좁은 제품 이름 → 가진 회사
    rel: dict[tuple[int, int], set[str]]            # (작은 id, 큰 id) → 관계 종류
    markets: dict[int, str] = field(default_factory=dict)   # 기업 → 코스피, 코스닥, 코넥스
    near: dict[int, set[int]] = field(default_factory=dict)
    hubs: set[int] = field(default_factory=set)

    def __post_init__(self):
        degree = collections.Counter(i for pair in self.rel for i in pair)
        self.hubs = {i for i, n in degree.items() if n > HUB}
        self.near = collections.defaultdict(set)
        for a, b in self.rel:
            if a not in self.hubs and b not in self.hubs:
                self.near[a].add(b)
                self.near[b].add(a)


def load_links(db, as_of: date | None = None) -> Links:
    """as_of 를 주면 그날까지 공시된 것만 쓴다. 안 주면 지금 유효한 것 전부."""
    companies = [c for c in db.scalars(select(Company).where(Company.stock_code.isnot(None))) if "스팩" not in c.label]
    listed = {c.company_id: c.label for c in companies}
    markets = {c.company_id: MARKET_NAMES[c.corp_cls] for c in companies if c.corp_cls in MARKET_NAMES}
    relations = select(Relation.subject_company_id, Relation.object_company_id, Relation.rel_type).where(
        Relation.retired_at.is_(None), Relation.subject_company_id.isnot(None), Relation.object_company_id.isnot(None))
    latest = select(Product.company_id, func.max(Product.rcept_no).label("rcept_no"))
    if as_of:
        relations = relations.where(Relation.disclosed_date <= as_of, or_(Relation.invalidated_date.is_(None), Relation.invalidated_date > as_of))
        latest = latest.where(Product.disclosed_date <= as_of)
    else:
        relations = relations.where(Relation.invalidated_date.is_(None))
    rel = collections.defaultdict(set)
    for subject, target, kind, attrs in db.execute(relations.add_columns(Relation.attrs)):
        if (attrs or {}).get("exited"):   # 기간 중에 주식을 다 내놓은 주주의 0% 줄은 관계로 치지 않는다
            continue
        if subject != target and subject in listed and target in listed:
            rel[(min(subject, target), max(subject, target))].add(REL_NAMES.get(kind, kind))
    latest = latest.group_by(Product.company_id).subquery()
    holders = collections.defaultdict(set)
    for row in db.scalars(select(Product).join(latest, Product.rcept_no == latest.c.rcept_no)):
        if row.company_id in listed and (row.share_pct is None or row.share_pct >= MIN_SHARE):
            for name in row.std_names or []:
                if not name.endswith(("유통", "도소매")):   # 받아서 파는 것은 같은 제품을 만드는 무리가 아니다
                    holders[name].add(row.company_id)
    return Links(listed, {name: members for name, members in holders.items() if 2 <= len(members) <= NARROW}, dict(rel), markets)


def load_prices(db, start: date | None = None, end: date | None = None) -> tuple[list[date], dict[date, dict[int, tuple[float, float]]]]:
    """거래일 목록과, 날짜 → 기업 → (등락률 %, 거래대금)."""
    ids = {c.stock_code: c.company_id for c in db.scalars(select(Company).where(Company.stock_code.isnot(None)))}
    rows = select(PriceDaily.stock_code, PriceDaily.trade_date, PriceDaily.change_pct, PriceDaily.trade_value)
    if start:
        rows = rows.where(PriceDaily.trade_date >= start)
    if end:
        rows = rows.where(PriceDaily.trade_date <= end)
    prices = collections.defaultdict(dict)
    for code, day, change, value in db.execute(rows):
        if code in ids:
            prices[day][ids[code]] = (float(change or 0), float(value or 0))
    return sorted(prices), prices


def movers(days: list[date], prices: dict, k: int, links: Links, down: bool = False) -> tuple[set[int], dict[int, float], dict[int, float], float]:
    """k 번째 거래일에 볼 종목, 등락률, 시장 중앙값을 뺀 등락률, 오른 곳의 기준선.
    down 이면 내린 쪽을 본다: 셋째 값의 부호를 뒤집어, 많이 내린 곳이 큰 값이 되게 한다 (뒤의 규칙을 그대로 쓴다)."""
    day, before = days[k], days[max(0, k - LIQUID_DAYS):k]
    liquid = set()
    for company in prices[day]:
        values = [prices[d][company][1] for d in before if company in prices[d]]
        if company in links.names and len(values) >= LIQUID_DAYS * 3 // 4 and statistics.mean(values) >= LIQUID_VALUE:
            liquid.add(company)
    change = {company: prices[day][company][0] for company in liquid}
    if not change:
        return liquid, change, {}, FLOOR
    market = statistics.median(change.values())
    excess = {company: (market - value if down else value - market) for company, value in change.items()}
    line = max(FLOOR, sorted(excess.values(), reverse=True)[int(len(excess) * TOP)])
    return liquid, change, excess, line


def find_groups(excess: dict[int, float], line: float, liquid: set[int], links: Links) -> list[dict]:
    """오른 곳들을 이유 하나로 묶은 무리. 각 무리: kind(제품, 관계, 둘 다), why, members, of(그 이유를 가진 곳 전부의 수), third, grade."""
    hot = {company for company, value in excess.items() if value >= line}
    groups = []
    # 관계: 직접 이어진 오른 곳들
    parent = {company: company for company in hot}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = [(a, b, kinds) for (a, b), kinds in links.rel.items() if a in hot and b in hot and a not in links.hubs and b not in links.hubs]
    for a, b, _ in pairs:
        parent[root(a)] = root(b)
    used = collections.defaultdict(collections.Counter)
    for a, _, kinds in pairs:
        used[root(a)].update(kinds)
    joined = collections.defaultdict(set)
    for company in hot:
        joined[root(company)].add(company)
    for key, members in joined.items():
        if len(members) >= 2:
            around = (set().union(*(links.near.get(m, set()) for m in members)) | members) & liquid   # 이 무리와 직접 이어진 상장사 전부
            groups.append({"kind": "관계", "why": [kind for kind, _ in used[key].most_common(3)], "members": members, "of": len(around)})
    # 제품: 좁은 제품 이름 하나를 가진 곳 가운데 3분의 1 이상이 올랐다
    for name, holders in links.holders.items():
        live = holders & liquid
        up = live & hot
        if len(up) >= 2 and len(up) * 3 >= len(live):
            groups.append({"kind": "제품", "why": [name], "members": up, "of": len(live)})
    # 구성이 거의 같은 무리는 하나로 합친다 (전선 회사들이 "전력 케이블"과 "절연 전선" 두 줄로 나오지 않게)
    groups.sort(key=lambda g: -len(g["members"]))
    merged = []
    for group in groups:
        for other in merged:
            both = len(group["members"] & other["members"])
            if both / len(group["members"] | other["members"]) >= 0.6 or (both == len(group["members"]) and both >= 2):
                other["why"] += [why for why in group["why"] if why not in other["why"]]
                if group["kind"] != other["kind"]:
                    other["kind"] = "둘 다"
                break
        else:
            merged.append(group)
    out = []
    for group in merged:
        top = sorted((excess[m] for m in group["members"]), reverse=True)
        half = len(top) * 2 >= group["of"]
        # 2곳짜리는 우연과 구분되지 않아 버린다. 3곳짜리는 그 이유를 가진 곳의 절반 이상이 올랐을 때만 둔다
        if len(top) >= 3 and top[2] >= STRONG and (half or len(top) >= 4):
            out.append({**group, "third": top[2], "grade": CLEAR if half and len(top) >= 4 else FAIR})
    return sorted(out, key=lambda g: (g["grade"] != CLEAR, -len(g["members"]), -g["third"]))


def _side(days: list[date], prices: dict, k: int, links: Links, down: bool) -> dict:
    liquid, change, excess, line = movers(days, prices, k, links, down)
    groups = find_groups(excess, line, liquid, links)
    grouped = set().union(*(g["members"] for g in groups)) if groups else set()
    alone = sorted((c for c, value in excess.items() if value >= max(line, SOLO) and c not in grouped), key=lambda c: -excess[c])
    return {"hot": sum(1 for value in excess.values() if value >= line),
            "groups": [{"grade": g["grade"], "kind": g["kind"], "why": g["why"][:4], "n": len(g["members"]), "of": g["of"], "third": round(g["third"], 1),
                        "members": [{"id": m, "name": links.names[m], "market": links.markets.get(m), "change": change[m]}
                                    for m in sorted(g["members"], key=lambda m: -excess[m])]}
                       for g in groups],
            "alone": [{"id": c, "name": links.names[c], "market": links.markets.get(c), "change": change[c]} for c in alone]}


def day_result(days: list[date], prices: dict, k: int, links: Links) -> dict:
    """오른 쪽은 맨 위에(hot, groups, alone), 내린 쪽은 down 아래에 같은 모양으로."""
    liquid, change, _, _ = movers(days, prices, k, links)
    return {"day": days[k].isoformat(), "market": round(statistics.median(change.values()), 2) if change else None, "watched": len(liquid),
            **_side(days, prices, k, links, False), "down": _side(days, prices, k, links, True)}


def store(db, days: list[date], prices: dict, links: Links, start: date, end: date) -> int:
    """기간의 날마다 결과를 hot_day 에 넣는다. 혼자 오른 곳에는 그 무렵에 나온 공시를 붙인다."""
    count = 0
    before = db.scalar(select(HotDay).where(HotDay.trade_date < start).order_by(HotDay.trade_date.desc()).limit(1))
    follows = before and days.index(before.trade_date) + 1 < len(days) and days[days.index(before.trade_date) + 1] >= start
    previous = [before.payload["groups"], before.payload.get("down", {}).get("groups", [])] if follows else [[], []]
    for k, day in enumerate(days):
        if k < LIQUID_DAYS or not start <= day <= end:
            continue
        result = day_result(days, prices, k, links)
        for number, side in enumerate((result, result["down"])):
            for group in side["groups"]:   # 앞 거래일에도 절반 이상 같은 구성으로 움직였으면 이어진 것으로 센다
                ids = {member["id"] for member in group["members"]}
                same = [g for g in previous[number] if len(ids & {m["id"] for m in g["members"]}) * 2 >= len(ids)]
                group["streak"] = 1 + max((g.get("streak", 1) for g in same), default=0)
            previous[number] = side["groups"]
            side["alone_total"] = len(side["alone"])
            side["alone"] = side["alone"][:ALONE_SHOWN]
            filings = collections.defaultdict(list)
            ids = [item["id"] for item in side["alone"]]
            if ids:
                for doc in db.scalars(select(Document).where(Document.company_id.in_(ids), Document.rcept_dt <= day,
                                                             Document.rcept_dt > day - timedelta(days=FILING_DAYS)).order_by(Document.rcept_no.desc())):
                    filings[doc.company_id].append({"title": doc.report_nm.strip(), "rcept_no": doc.rcept_no, "date": doc.rcept_dt.isoformat()})
            for item in side["alone"]:
                item["filings"] = filings[item["id"]][:3]
        row = db.get(HotDay, day)
        if row is not None:   # 다시 계산해도 이미 찾아 둔 기사(hot_news.py)는 구성이 같은 종목군에 그대로 둔다
            for new, old in ((result, row.payload), (result["down"], row.payload.get("down") or {})):
                found = {frozenset(m["id"] for m in g["members"]): g["news"] for g in old.get("groups", []) if "news" in g}
                for group in new["groups"]:
                    news = found.get(frozenset(m["id"] for m in group["members"]))
                    if news:
                        group["news"] = news
        if row is None:
            db.add(HotDay(trade_date=day, payload=result, computed_at=datetime.now()))
        else:
            row.payload, row.computed_at = result, datetime.now()
        count += 1
    db.commit()
    return count


def check(days: list[date], prices: dict, links: Links, start: date, end: date, shuffles: int = 8, seed: int = 5, down: bool = False) -> dict:
    """기간 전체를 돌리고, 등락률은 그대로 둔 채 어느 회사의 것인지만 섞었을 때 나오는 무리 수와 견준다."""
    rnd = random.Random(seed)
    real, chance, results = collections.Counter(), collections.Counter(), []
    for k, day in enumerate(days):
        if k < LIQUID_DAYS or not start <= day <= end:
            continue
        liquid, _, excess, line = movers(days, prices, k, links, down)
        result = day_result(days, prices, k, links)
        result = result["down"] | {"day": result["day"]} if down else result
        results.append(result)
        real.update((g["grade"], g["kind"]) for g in result["groups"])
        order = sorted(excess)
        values = [excess[c] for c in order]
        for _ in range(shuffles):
            rnd.shuffle(values)
            for g in find_groups(dict(zip(order, values)), line, liquid, links):
                chance[(g["grade"], g["kind"])] += 1 / shuffles
    return {"days": results, "real": real, "chance": chance}


def _print_day(result: dict):
    print(f"{result['day']} | 시장 중앙값 {result['market']:+.2f}% | 본 종목 {result['watched']} | 오른 곳 {result['hot']} | 무리 {len(result['groups'])} | 혼자 오른 곳 {len(result['alone'])}")
    for g in result["groups"]:
        print(f"  [{g['grade']}·{g['kind']}] {' / '.join(g['why'])} | {g['of']}곳 중 {g['n']}곳 | "
              + ", ".join(f"{m['name']} {m['change']:+.1f}" for m in g["members"][:6]))


def _print_check(outcome: dict):
    days, real, chance = outcome["days"], outcome["real"], outcome["chance"]
    print(f"거래일 {len(days)} ({days[0]['day']} ~ {days[-1]['day']})")
    for grade in (CLEAR, FAIR):
        for kind in ("제품", "관계", "둘 다"):
            r, c = real[(grade, kind)], chance[(grade, kind)]
            print(f"  {grade} {kind}: 실제 {r} / 섞었을 때 {c:.0f} ({r / max(c, 0.1):.1f}배)")
        r = sum(v for (g, _), v in real.items() if g == grade)
        c = sum(v for (g, _), v in chance.items() if g == grade)
        per = [sum(1 for g in d["groups"] if g["grade"] == grade) for d in days]
        print(f"  {grade} 합계: 실제 {r} / 섞었을 때 {c:.0f} ({r / max(c, 0.1):.1f}배, 우연으로 볼 몫 {c / max(r, 1):.0%}) · 하루 평균 {statistics.mean(per):.1f}개, 없는 날 {sum(1 for p in per if not p)}/{len(days)}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    save = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--save=")), None)
    with session() as db:
        links = load_links(db)
        days, prices = load_prices(db)
        if args and args[0] == "store":
            last = db.scalar(select(func.max(HotDay.trade_date)))
            start, end = (date.fromisoformat(a) for a in args[1:3]) if len(args) >= 3 else ((last + timedelta(days=1)) if last else days[0], days[-1])
            print(f"{start} ~ {end}: {store(db, days, prices, links, start, end)}일")
            return
    if args and args[0] == "check":
        start, end = (date.fromisoformat(a) for a in args[1:3])
        outcome = check(days, prices, links, start, end, down="--down" in sys.argv)
        _print_check(outcome)
        if save:
            with open(save, "w", encoding="utf-8") as file:
                json.dump(outcome["days"], file, ensure_ascii=False)
    else:
        day = date.fromisoformat(args[0]) if args else days[-1]
        _print_day(day_result(days, prices, days.index(day), links))


if __name__ == "__main__":
    main()
