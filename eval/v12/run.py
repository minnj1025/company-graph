"""평가 12판: 핵심 뉴스 시점의 연관 종목 수익률. 규칙은 README.md 에 수익률을 보기 전에 적어 두었다.

실행: python -m eval.v12.run groups     사건과 묶음만 만든다(수익률 없이). 묶음이 제대로 골라졌는지 보는 데 쓴다
      python -m eval.v12.run returns    수익률과 판정까지. results.json, results.md 를 쓴다
"""
import bisect
import collections
import json
import math
import random
import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from company_graph.config import CACHE_DIR
from company_graph.db import BusinessSection, Company, Product, Relation, session

OUT = CACHE_DIR / "v12"
HERE = Path(__file__).resolve().parent
IMP, DEDUP, SIZE = 0.7, 5, 5
HORIZONS = (1, 3, 7)
LIQUID_DAYS, LIQUID_VALUE, LIQUID_MIN = 20, 1e8, 15     # hot.py 와 같다
HUB, NARROW, MIN_SHARE = 10, 12, 10                      # hot.py 와 같다
CONTRACT_DAYS = 365
FIRST, LAST = date(2025, 7, 1), date(2026, 10, 24)
PRIORITY = {"supply": 1, "equity": 2, "product": 3}


# ---------- 시세 ----------
class Prices:
    def __init__(self):
        self.days, self.rows = [], []
        for path in sorted((OUT / "krx").glob("*.json")):
            d = path.stem
            self.days.append(date(int(d[:4]), int(d[4:6]), int(d[6:])))
            self.rows.append(json.loads(path.read_text(encoding="utf-8")))
        self._market = {}

    def entry(self, published: date) -> int | None:
        """발행일 다음 거래일의 번호."""
        i = bisect.bisect_right(self.days, published)
        return i if i < len(self.days) else None

    def ret(self, code: str, i: int, h: int) -> float | None:
        """i 거래일 시가에 사서 h 거래일째 종가에 판 수익률. 이튿날부터는 등락률(기준가 조정 반영)을 이어 붙인다."""
        if i + h - 1 >= len(self.days):
            return None
        first = self.rows[i].get(code)
        if not first or not first[0]:
            return None
        r = first[1] / first[0]
        for k in range(1, h):
            row = self.rows[i + k].get(code)
            if not row:
                return None
            r *= 1 + row[5] / 100
        return r - 1

    def market(self, i: int, h: int, market: str) -> float | None:
        key = (i, h, market)
        if key not in self._market:
            num = den = 0.0
            for code, prev in self.rows[i - 1].items():
                if prev[4] != market or not prev[2]:
                    continue
                r = self.ret(code, i, h)
                if r is not None:
                    num += prev[2] * r
                    den += prev[2]
            self._market[key] = num / den if den else None
        return self._market[key]

    def excess(self, code: str, i: int, h: int) -> float | None:
        r = self.ret(code, i, h)
        row = self.rows[i].get(code)
        if r is None or not row:
            return None
        m = self.market(i, h, row[4])
        return None if m is None else r - m

    def liquid(self, code: str, i: int) -> bool:
        values = [self.rows[k][code][3] for k in range(max(0, i - LIQUID_DAYS), i) if code in self.rows[k]]
        return len(values) >= LIQUID_MIN and sum(values) / LIQUID_DAYS >= LIQUID_VALUE


# ---------- 글자 n그램 TF-IDF ----------
def grams(text: str) -> collections.Counter:
    text = re.sub(r"[\s·,()/\-]+", " ", text.lower()).strip()
    out = collections.Counter()
    for word in text.split():
        if len(word) < 2:
            out[word] += 1
        for n in (2, 3):
            out.update(word[k:k + n] for k in range(len(word) - n + 1))
    return out


class Docs:
    """회사마다 그 시점의 최신 정기보고서에서: '사업의 개요'와 '주요 제품 및 서비스' 글(각 앞 2,500자)과 제품 표의 이름.
    인턴 때 사업 아이템 키워드를 뽑은 바로 그 두 소제목이다. 달마다 전달 말일 기준으로 묶는다(뒤의 보고서를 보지 않게)."""
    SECTIONS, CHARS, TOP = (1, 2), 2500, 300

    def __init__(self, db, ids: dict[int, str]):
        texts = collections.defaultdict(list)          # (회사, 접수번호) → 글 조각
        dated = {}                                      # (회사, 접수번호) → 공시일
        self.products = collections.defaultdict(list)   # (회사, 접수번호) → 제품 줄
        for p in db.scalars(select(Product).where(Product.company_id.in_(list(ids)))):
            key = (p.company_id, p.rcept_no)
            self.products[key].append(p)
            texts[key] += [p.name] + list(p.std_names or [])
            dated[key] = p.disclosed_date
        rows = db.execute(select(BusinessSection.company_id, BusinessSection.rcept_no, BusinessSection.disclosed_date, BusinessSection.text)
                          .where(BusinessSection.company_id.in_(list(ids)), BusinessSection.section_no.in_(self.SECTIONS)))
        for cid, rcept, disclosed, text in rows:
            texts[(cid, rcept)].append((text or "")[:self.CHARS])
            dated[(cid, rcept)] = disclosed
        counts = {key: grams(" ".join(parts)) for key, parts in texts.items()}
        df = collections.Counter()
        for g in counts.values():
            df.update(g.keys())
        self.n = len(counts)
        self.idf = {g: math.log((1 + self.n) / (1 + c)) + 1 for g, c in df.items()}
        self.vectors = {key: self.vector(g, top=self.TOP) for key, g in counts.items()}
        by_company = collections.defaultdict(list)
        for (cid, rcept), d in dated.items():
            by_company[cid].append((d, rcept))
        self.reports = {cid: sorted(v) for cid, v in by_company.items()}
        self._snap = {}

    def vector(self, g: collections.Counter, top: int | None = None) -> dict:
        v = {k: c * self.idf.get(k, math.log(1 + self.n) + 1) for k, c in g.items()}
        if top and len(v) > top:   # 무게가 큰 n그램만 남긴다 (메모리, 잡음)
            v = dict(sorted(v.items(), key=lambda kv: -kv[1])[:top])
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {k: x / norm for k, x in v.items()}

    def snapshot(self, as_of: date):
        """as_of(전달 말일)까지 공시된 최신 보고서로: n그램 → [(회사, 무게)], 좁은 제품 → 가진 회사, 회사 → 가진 제품."""
        if as_of in self._snap:
            return self._snap[as_of]
        self._snap.clear()   # 사건을 날짜 순서로 돌므로 하나만 둔다
        index, holders, owned = collections.defaultdict(list), collections.defaultdict(set), collections.defaultdict(set)
        for cid, reports in self.reports.items():
            usable = [rcept for d, rcept in reports if d <= as_of]
            if not usable:
                continue
            for g, w in self.vectors[(cid, usable[-1])].items():
                index[g].append((cid, w))
            product_reports = [rcept for d, rcept in reports if d <= as_of and (cid, rcept) in self.products]
            for r in self.products.get((cid, product_reports[-1]), []) if product_reports else []:
                if r.share_pct is None or float(r.share_pct) >= MIN_SHARE:
                    for name in r.std_names or []:
                        if not name.endswith(("유통", "도소매")):
                            holders[name].add(cid)
                            owned[cid].add(name)
        narrow = {name: members for name, members in holders.items() if 2 <= len(members) <= NARROW}
        self._snap[as_of] = (None, index, narrow, owned)
        return self._snap[as_of]

    def scores(self, as_of: date, words: list[str]) -> dict[int, float]:
        if not words:
            return {}
        _, index, _, _ = self.snapshot(as_of)
        q = self.vector(grams(" ".join(words)))
        out = collections.defaultdict(float)
        for g, w in q.items():
            for cid, dw in index.get(g, ()):
                out[cid] += w * dw
        return out


def month_end_before(d: date) -> date:
    return d.replace(day=1) - timedelta(days=1)


# ---------- 공시 연결 ----------
class Graph:
    def __init__(self, db, ids: dict[int, str]):
        self.by = collections.defaultdict(list)
        q = select(Relation.subject_company_id, Relation.object_company_id, Relation.rel_type, Relation.value_num,
                   Relation.disclosed_date, Relation.invalidated_date, Relation.attrs).where(
            Relation.retired_at.is_(None), Relation.rel_type.in_(("equity", "affiliate", "supply_contract")),
            Relation.subject_company_id.in_(list(ids)), Relation.object_company_id.in_(list(ids)))
        for a, b, kind, value, disclosed, invalidated, attrs in db.execute(q):
            if a == b or any((attrs or {}).get(k) for k in ("exited", "share_class", "treasury")):
                continue
            row = (a, b, kind, float(value or 0), disclosed, invalidated)
            self.by[a].append(row)
            self.by[b].append(row)

    def _alive(self, cid: int, as_of: date):
        for a, b, kind, value, disclosed, invalidated in self.by.get(cid, ()):
            if disclosed <= as_of and (invalidated is None or invalidated > as_of):
                if kind == "supply_contract" and disclosed < as_of - timedelta(days=CONTRACT_DAYS):
                    continue
                yield (b if a == cid else a), kind, value

    def degree(self, cid: int, as_of: date) -> int:
        """상장사끼리의 선이 몇 개인가 (hot.py 의 허브 판정과 같다)."""
        return len({other for other, _, _ in self._alive(cid, as_of)})

    def candidates(self, cid: int, as_of: date, narrow: dict, owned: dict) -> dict[int, tuple[int, float, str]]:
        """후보 → (우선순위, 세기, 까닭). ① 공급계약 상대(금액 합) ② 지분·계열(허브가 아닌 이웃, 지분율) ③ 좁은 제품을 함께 가진 회사."""
        out = {}
        supply, equity = collections.defaultdict(float), collections.defaultdict(float)
        for other, kind, value in self._alive(cid, as_of):
            if kind == "supply_contract":
                supply[other] += value
            else:
                equity[other] = max(equity[other], value)
        for other, amount in supply.items():
            out[other] = (PRIORITY["supply"], amount, "공급계약")
        for other, pct in equity.items():
            if other not in out and self.degree(other, as_of) <= HUB:
                out[other] = (PRIORITY["equity"], pct, "지분·계열")
        for name in sorted(owned.get(cid, ()), key=lambda n: len(narrow.get(n, ())) or 99):
            for other in narrow.get(name, ()):
                if other != cid and other not in out:
                    out[other] = (PRIORITY["product"], -len(narrow[name]), f"제품 {name}")
        return out


# ---------- 사건 ----------
def load_labels(second_stage: bool = True) -> dict[str, list[dict]]:
    """1단계(제목) 판정 위에 2단계(본문) 판정을 덮는다. 본문이 제목과 다른 기사였으면 2단계도 제목만으로 판정한 것이다.
    second_stage=False 면 제목 단계 판정만 쓴다(비교용)."""
    out = {}
    for path in (OUT / "labels").glob("*.json"):
        second_path = OUT / "labels2" / path.name
        second = json.loads(second_path.read_text(encoding="utf-8")) if second_stage and second_path.exists() else {}
        rows = []
        for chunk in json.loads(path.read_text(encoding="utf-8")).values():
            for r in chunk:
                if r.get("labeled"):
                    s = second.get(r["title"])
                    rows.append({**r, **s, "stage": 2} if s else {**r, "stage": 1})
        out[path.stem] = sorted(rows, key=lambda r: r["date"])
    return out


def events(labels: dict, prices: Prices, kind: str) -> list[dict]:
    def keep(r):
        if not r.get("about"):
            return False
        core = r.get("cat") == "산업" and (r.get("imp") or 0) >= IMP
        if kind == "good":
            return core and r.get("dir") == 1
        if kind == "bad":
            return core and r.get("dir") == -1
        return not (core and r.get("dir") == 1)   # 'other': 핵심(좋은 소식)이 아닌 뉴스
    out = []
    for code, rows in labels.items():
        last = -99
        for r in rows:
            d = date.fromisoformat(r["date"])
            if not (FIRST <= d <= LAST) or not keep(r):
                continue
            i = prices.entry(d)
            if i is None or i + max(HORIZONS) - 1 >= len(prices.days) or code not in prices.rows[i]:
                continue
            if i - last < DEDUP:
                continue
            last = i
            out.append({"code": code, "date": r["date"], "entry": i, "title": r["title"], "kw": r.get("kw") or [],
                        "imp": r.get("imp"), "kind": r.get("kind"), "stage": r.get("stage", 1)})
    return sorted(out, key=lambda e: (e["entry"], e["code"]))


def build_groups(evts: list[dict], prices: Prices, docs: Docs, graph: Graph, code_of: dict, id_of: dict, names: dict):
    for e in evts:
        cid = id_of.get(e["code"])
        published = date.fromisoformat(e["date"])
        as_of, snap_day = published - timedelta(days=1), month_end_before(published)
        _, _, narrow, owned = docs.snapshot(snap_day)
        tradable = lambda c: c != cid and c in code_of and prices.liquid(code_of[c], e["entry"])
        sim = docs.scores(snap_day, e["kw"])
        b = sorted((c for c, s in sim.items() if s > 0 and tradable(c)), key=lambda c: -sim[c])[:SIZE]
        cand = {c: v for c, v in graph.candidates(cid, as_of, narrow, owned).items() if tradable(c)} if cid else {}
        c_kw = sorted(cand, key=lambda c: (-round(sim.get(c, 0.0), 6), cand[c][0], -cand[c][1]))[:SIZE]
        c0 = sorted(cand, key=lambda c: (cand[c][0], -cand[c][1]))[:SIZE]
        # C12: ③(제품) 후보를 뺀 공시 관계만. hot.py 의 제품 규칙을 2026년 시세로 정했기 때문에 따로 본다 (수익률 계산 전에 더함)
        c12 = sorted((c for c in cand if cand[c][0] < PRIORITY["product"]), key=lambda c: (-round(sim.get(c, 0.0), 6), cand[c][0], -cand[c][1]))[:SIZE]
        e["groups"] = {"B": [code_of[c] for c in b], "C": [code_of[c] for c in c_kw], "C0": [code_of[c] for c in c0],
                       "C12": [code_of[c] for c in c12]}
        e["why"] = {code_of[c]: cand[c][2] for c in set(c_kw) | set(c0)}
        e["name"] = names.get(cid, e["code"])
    return evts


# ---------- 통계 ----------
def clustered(values: list[tuple[int, float]]) -> dict:
    """산 날별 평균을 낸 뒤 날들 사이의 평균과 t."""
    by_day = collections.defaultdict(list)
    for day, v in values:
        by_day[day].append(v)
    means = [sum(v) / len(v) for v in by_day.values()]
    n = len(means)
    if n < 3:
        return {"events": len(values), "days": n}
    m, sd = statistics.mean(means), statistics.stdev(means)
    return {"events": len(values), "days": n, "mean_pct": round(100 * m, 3), "t": round(m / (sd / math.sqrt(n)), 2) if sd else None,
            "hit_rate": round(sum(1 for _, v in values if v > 0) / len(values), 3)}


def group_excess(prices: Prices, codes: list[str], i: int, h: int) -> float | None:
    vals = [x for x in (prices.excess(c, i, h) for c in codes) if x is not None]
    return sum(vals) / len(vals) if vals else None


def measure(evts: list[dict], prices: Prices) -> dict:
    out = {}
    for h in HORIZONS:
        rows = collections.defaultdict(list)
        for e in evts:
            i = e["entry"]
            main = prices.excess(e["code"], i, h)
            if main is not None:
                rows["main"].append((i, main))
            got = {k: group_excess(prices, e["groups"][k], i, h) for k in ("B", "C", "C0", "C12")}
            for k, v in got.items():
                if v is not None:
                    rows[k].append((i, v))
            if got["B"] is not None and got["C"] is not None:
                rows["C-B"].append((i, got["C"] - got["B"]))
            if got["B"] is not None and got["C0"] is not None:
                rows["C0-B"].append((i, got["C0"] - got["B"]))
            if got["B"] is not None and got["C12"] is not None:
                rows["C12-B"].append((i, got["C12"] - got["B"]))
        out[f"h{h}"] = {k: clustered(v) for k, v in rows.items()}
    return out


def verdict(full: dict) -> str:
    ok = [full[f"h{h}"].get("C-B", {}) for h in (3, 7)]
    good = [bool(r.get("mean_pct", 0) > 0 and (r.get("t") or 0) >= 2) for r in ok]
    if all(good):
        return "Corpath 가 낫다 (h=3, h=7 모두 평균 > 0, t ≥ 2)"
    if any(good):
        return "일부에서만 나음 (h=3, h=7 가운데 하나만 기준을 넘음)"
    return "차이를 보이지 못함 (h=3, h=7 모두 기준 미달)"


def cases(evts: list[dict], prices: Prices, names_by_code: dict, k: int = 5, seed: int = 12) -> list[dict]:
    pool = [e for e in evts if e["groups"]["B"] and e["groups"]["C"]]
    out = []
    for e in sorted(random.Random(seed).sample(pool, min(k, len(pool))), key=lambda e: e["entry"]):
        row = {"date": e["date"], "entry_day": prices.days[e["entry"]].isoformat(), "company": e["name"], "title": e["title"], "kw": e["kw"]}
        for h in (3, 7):
            market = prices.market(e["entry"], h, prices.rows[e["entry"]][e["code"]][4])
            row[f"market_h{h}_pct"] = round(100 * market, 2) if market is not None else None
        row["stocks"] = {}
        for g in ("main", "B", "C"):
            codes = [e["code"]] if g == "main" else e["groups"][g]
            row["stocks"][g] = [{"name": names_by_code.get(c, c), "why": e["why"].get(c),
                                 **{f"h{h}_pct": (round(100 * r, 2) if (r := prices.ret(c, e["entry"], h)) is not None else None) for h in (3, 7)}}
                                for c in codes]
        out.append(row)
    return out


def setup():
    prices = Prices()
    with session() as db:
        firms = [c for c in db.scalars(select(Company).where(Company.stock_code.isnot(None))) if "스팩" not in c.label]
        code_of = {c.company_id: c.stock_code for c in firms}
        id_of = {c.stock_code: c.company_id for c in firms}
        names = {c.company_id: c.label for c in firms}
        docs, graph = Docs(db, code_of), Graph(db, code_of)
    return prices, docs, graph, code_of, id_of, names


def main():
    step = sys.argv[1]
    prices, docs, graph, code_of, id_of, names = setup()
    labels = load_labels()
    names_by_code = {code_of[c]: n for c, n in names.items()}
    result = {"rules": {"imp": IMP, "dedup_days": DEDUP, "size": SIZE, "horizons": HORIZONS, "period": [FIRST.isoformat(), LAST.isoformat()]}}
    sets = {}
    for kind in ("good", "bad", "other"):
        sets[kind] = build_groups(events(labels, prices, kind), prices, docs, graph, code_of, id_of, names)
        filled = {g: sum(1 for e in sets[kind] if e["groups"][g]) for g in ("B", "C", "C0", "C12")}
        print(kind, "사건", len(sets[kind]), "묶음이 빈 것 아님", filled, flush=True)
    (OUT / "events.json").write_text(json.dumps(sets, ensure_ascii=False, default=str), encoding="utf-8")
    if step != "returns":
        for e in random.Random(3).sample(sets["good"], min(8, len(sets["good"]))):
            print(e["date"], e["name"], e["title"][:50], e["kw"])
            for g in ("B", "C", "C0", "C12"):
                print("   ", g, [(names_by_code.get(c, c), e["why"].get(c, "")) for c in e["groups"][g]])
        return
    result["good"] = {"full": measure(sets["good"], prices),
                      "2025-07~12": measure([e for e in sets["good"] if e["date"] < "2026-01-01"], prices),
                      "2026-01~10": measure([e for e in sets["good"] if e["date"] >= "2026-01-01"], prices)}
    result["bad"] = {"full": measure(sets["bad"], prices)}
    result["other_main_only"] = {"full": measure(sets["other"], prices)}
    result["verdict"] = verdict(result["good"]["full"])
    result["cases"] = cases(sets["good"], prices, names_by_code)
    result["counts"] = {k: len(v) for k, v in sets.items()}
    result["good_by_stage"] = dict(collections.Counter(f"stage{e.get('stage', 1)}" for e in sets["good"]))
    # 비교용: 본문 단계 없이 제목 단계 판정만으로 고른 핵심 뉴스
    title_only = build_groups(events(load_labels(second_stage=False), prices, "good"), prices, docs, graph, code_of, id_of, names)
    result["title_only"] = {"events": len(title_only), "full": measure(title_only, prices)}
    result["title_only"]["verdict"] = verdict(result["title_only"]["full"])
    (HERE / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("verdict", "counts")}, ensure_ascii=False))
    for h in HORIZONS:
        print(h, json.dumps(result["good"]["full"][f"h{h}"], ensure_ascii=False))


if __name__ == "__main__":
    main()
