"""평가 문항 4판에 쓸 자료 묶음을 뽑는다. 여러 조회를 엮어야 답할 수 있는 문항을 겨냥한다.

2판(eval/v2/draw.py)과 같은 원칙이다. 묶음에 넣는 것은 추출기를 거치지 않은 원자료다:
OpenDART API 응답 그대로(최대주주 현황, 타법인출자 현황), 공시 원문의 표 칸을 순서대로 이은 글, 사업보고서의 계열회사 표 칸.
관계 표(relation)는 쓰지 않는다. 어느 기업·공시를 고를지는 공시 목록(document 표)과 원자료의 글자를 직접 견주어 정한다.
1판과 2판에 쓴 기업은 다시 쓰지 않고, 한 기업은 4판 안에서도 한 묶음에만 나온다.

외부 호출이 일어나지 않게 캐시에 저장된 파일만 읽는다(없으면 그 기업·공시는 건너뛴다).
종류마다 12개를 뽑는다. 문항은 그 가운데 좋은 것 8개로 쓰고, 나머지는 gold 의 skip 에 이유를 적는다.

실행: python -m eval.v4.draw   →  eval/v4/packets/*.json
"""
import html
import json
import random
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from company_graph.config import CACHE_DIR
from company_graph.db import Company, Document, session
from company_graph.supply_parser import cells

SEED = 20261012
PER_KIND = 12
OUT = Path(__file__).parent / "packets"
V2_PACKETS = Path(__file__).parent.parent / "v2" / "packets"
LISTED = ("Y", "K", "N")
V1 = {"012630", "178320", "439260", "412350", "012030", "272210", "147830", "375500", "234920", "016380", "016450",
      "007460", "076610", "229500", "019770", "069260", "011320", "028260", "052420", "462860", "084670", "024110",
      "219420", "003670", "064550"}   # 1판에 쓴 기업
TOTAL_NAMES = {"계", "합계", "소계", "총계"}


def norm(name: str | None) -> str:
    return re.sub(r"\(주\)|㈜|주식회사|\(유\)|\s", "", name or "")


def num(value: str | None) -> float | None:
    cleaned = (value or "").replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def is_total(row: dict) -> bool:
    return norm(row.get("nm")) in TOTAL_NAMES


def cached_list(namespace: str, corp_code: str, year: int) -> list[dict] | None:
    """OpenDART 응답 캐시. 없으면 None (DART를 부르지 않는다)."""
    path = CACHE_DIR / namespace / f"{corp_code}_{year}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def shareholders(company: Company, year: int) -> list[dict]:
    return cached_list("dart_largest_shareholders", company.corp_code, year) or []


def investments(company: Company, year: int) -> list[dict]:
    return cached_list("dart_other_corp_investments", company.corp_code, year) or []


def raw_document(rcept_no: str) -> bytes | None:
    path = CACHE_DIR / "dart_document" / f"{rcept_no}.xml"
    return path.read_bytes() if path.exists() else None


def doc_text(rcept_no: str, limit: int = 3800) -> str | None:
    """공시 원문의 표 칸을 순서대로. 추출기의 양식 해석은 쓰지 않는다."""
    raw = raw_document(rcept_no)
    return None if raw is None else " | ".join(c[:160] for c in cells(raw))[:limit]


def affiliate_cells(rcept_no: str, limit: int = 5000) -> str | None:
    raw = raw_document(rcept_no)
    if raw is None:
        return None
    found = re.search(r'<TABLE-GROUP[^>]*ACLASS="AFF_CMP"[^>]*>(.*?)</TABLE-GROUP>', raw.decode("utf-8", "replace"), re.S)
    if not found:
        return None
    parts = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in re.findall(r"<T[DHEU]\b[^>]*>(.*?)</T[DHEU]>", found.group(1), re.S)]
    return " | ".join(p for p in parts if p)[:limit]


def excluded_before() -> tuple[set[str], set[str]]:
    """1판·2판에 쓴 기업: 종목코드와 (2판 T07의 공시 회사) 이름."""
    codes, names = set(V1), set()
    for path in V2_PACKETS.glob("*.json"):
        packet = json.loads(path.read_text(encoding="utf-8"))
        codes.add(packet["company"]["stock_code"])
        if packet.get("largest_shareholder"):
            codes.add(packet["largest_shareholder"].get("stock_code"))
        names.update(norm(f["filer"]) for f in packet.get("filings", []) if f.get("filer"))
    return codes, names


def main():
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    packets = []
    old_codes, old_names = excluded_before()

    def add(kind: str, task: str, company: Company, **body):
        number = sum(1 for p in packets if p["type"] == kind) + 1
        packet = {"id": f"{kind}-{number:02d}", "type": kind, "task": task,
                  "company": {"name": company.name, "stock_code": company.stock_code, "group": company.ftc_group}, **body}
        packets.append(packet)
        (OUT / f"{packet['id']}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    with session() as db:
        companies = {c.company_id: c for c in db.scalars(select(Company))}
        listed = sorted(i for i, c in companies.items() if c.corp_cls in LISTED and c.corp_code
                        and c.stock_code not in old_codes and norm(c.name) not in old_names)
        listed_set = set(listed)
        docs = [d for d in db.scalars(select(Document).order_by(Document.rcept_no)) if d.company_id in listed_set]
        supply = [d for d in docs if d.doc_type == "supply_contract"]
        annual = {(d.company_id, d.bsns_year): d for d in docs if d.doc_type == "annual"}
        by_no = {d.rcept_no: d for d in docs}
        by_name = {norm(companies[i].name): companies[i] for i in listed}
        used: set[int] = set()

        def filing(d: Document, limit: int = 3800) -> dict:
            return {"rcept_no": d.rcept_no, "filed": d.rcept_dt.isoformat(), "report": d.report_nm, "cells": doc_text(d.rcept_no, limit)}

        def chain(d: Document) -> list[Document]:
            out = [d]
            while out[0].corrects_rcept_no in by_no:
                out.insert(0, by_no[out[0].corrects_rcept_no])
            return out

        def holder_rows(company: Company, year: int) -> list[dict]:
            return [r for r in shareholders(company, year) if not is_total(r)]

        # X1: 최대주주가 상장사 B인 상장사 A. A의 최대주주 현황과 B의 타법인출자 현황에서 A를 적은 줄
        pairs = []
        for i in rng.sample(listed, len(listed)):
            a = companies[i]
            rows = holder_rows(a, 2025)
            top = max(rows, key=lambda r: num(r.get("trmend_posesn_stock_qota_rt")) or 0, default=None)
            b = top and by_name.get(norm(top["nm"]))
            if not b or b.company_id == a.company_id or a.company_id in used or b.company_id in used:
                continue
            a_rows = {y: shareholders(a, y) for y in (2024, 2025)}
            b_rows = {y: [r for r in investments(b, y) if norm(r.get("inv_prm")) == norm(a.name)] for y in (2024, 2025)}
            if not all(a_rows.values()) or not all(b_rows.values()):
                continue
            used.update((a.company_id, b.company_id))
            pairs.append((a, b, a_rows, b_rows))
            if len(pairs) == PER_KIND:
                break
        for a, b, a_rows, b_rows in pairs:
            add("X1", "두 보고서를 대조하는 문항이다. 이 회사(A)의 최대주주는 상장사 B다. A가 자기 사업보고서의 최대주주 현황에 적은 B의 주식 수·지분율(기초, 기말)과, "
                      "B가 자기 사업보고서의 타법인출자 현황에 적은 A의 주식 수·지분율(기초, 기말)이 같은지, 다르면 어느 값이 어떻게 다른지를 묻는다. "
                      "2024·2025 사업연도 가운데 하나를 고르거나 둘을 함께 본다. B의 타법인출자 현황은 A를 적은 줄만 넣었다(전체 줄 수는 rows_total).",
                a, largest_shareholders=a_rows,
                largest_shareholder={"name": b.name, "stock_code": b.stock_code,
                                     "other_corp_investments_rows_for_company": b_rows,
                                     "rows_total": {y: len(investments(b, y)) for y in (2024, 2025)}})

        # X4: 공급계약의 상대가 그 회사의 주주이거나 출자 대상인 경우 (상대 칸의 이름을 원자료의 이름과 글자로 견준다)
        yearly: dict[int, list[Document]] = defaultdict(list)
        for d in supply:
            if d.rcept_dt.year == 2025 and "해지" not in d.report_nm and not re.search(r"자회사|종속회사", d.report_nm):
                yearly[d.company_id].append(d)
        linked = []
        for i in sorted(yearly):
            a, ds = companies[i], yearly[i]
            if i in used or not 1 <= len(ds) <= 8 or not 1 <= len(investments(a, 2025)) <= 60:
                continue
            tied = {norm(r.get("nm")) for y in (2024, 2025) for r in holder_rows(a, y)} | \
                   {norm(r.get("inv_prm")) for y in (2024, 2025) for r in investments(a, y)}
            tied.discard("")
            # 알려진 흠: 출자 현황의 빈 줄('-')이 tied 에 남아, 상대를 '-'로 가린 계약이 함께 뽑힌다(X4-02).
            # 뽑은 묶음으로 이미 문항을 썼으므로 고치지 않고 둔다. 고치면 뒤따르는 종류의 추출 결과까지 달라진다.
            for d in ds:
                party = re.search(r"3\. 계약상대(?:방)? \| ([^|]+)", doc_text(d.rcept_no, 2600) or "")
                if party and norm(re.sub(r"\([^)]*\)$", "", party.group(1).strip())) in tied or party and norm(party.group(1)) in tied:
                    linked.append(i)
                    break
        for i in rng.sample(linked, len(linked))[:PER_KIND]:
            used.add(i)
            a, report = companies[i], annual.get((i, 2025))
            add("X4", "두 종류의 관계를 엮는 문항이다. 이 회사가 2025년에 낸 공급계약 공시 전부(정정 포함)와, 사업보고서의 최대주주 현황·타법인출자 현황·계열회사 표다. "
                      "2025년에 계약을 공시한 상대 가운데 이 회사와 지분으로 이어진 곳(이 회사의 주주이거나 이 회사가 출자한 곳)이 어디인지, 그 지분율이 얼마인지를 묻는다. "
                      "계약 원문의 \"회사와의 관계\" 칸만이 아니라 주주·출자 표와 맞춰 봐야 답할 수 있게 쓴다.",
                a, year=2025, filings=[filing(d, 1800) for d in yearly[i]],
                largest_shareholders={y: shareholders(a, y) for y in (2024, 2025)},
                other_corp_investments={y: investments(a, y) for y in (2024, 2025)},
                annual_report_2025=report and report.rcept_no,
                affiliate_table_2025_cells=affiliate_cells(report.rcept_no) if report else None)

        # X5: 최대주주 현황 표의 가장자리 (2025 사업연도)
        edges: dict[str, list[int]] = {"zero_rate": [], "zero_end": [], "total_mismatch": []}
        for i in listed:
            if i in used:
                continue
            rows = shareholders(companies[i], 2025)
            people = [r for r in rows if not is_total(r)]
            if not 3 <= len(people) <= 40:
                continue
            if any((num(r.get("trmend_posesn_stock_co")) or 0) > 0 and not (num(r.get("trmend_posesn_stock_qota_rt")) or 0) for r in people):
                edges["zero_rate"].append(i)
            if any((num(r.get("bsis_posesn_stock_co")) or 0) > 0 and not (num(r.get("trmend_posesn_stock_co")) or 0) for r in people):
                edges["zero_end"].append(i)
            totals = [r for r in rows if is_total(r)]
            kinds = {r.get("stock_knd") for r in totals}
            if totals and len(kinds) == len(totals):
                for t in totals:
                    same = sum(num(r.get("trmend_posesn_stock_co")) or 0 for r in people if r.get("stock_knd") == t.get("stock_knd"))
                    if same and (num(t.get("trmend_posesn_stock_co")) or 0) != same:
                        edges["total_mismatch"].append(i)
                        break
        tasks = {"zero_rate": "지분율이 0.00(또는 빈칸)으로 찍혔지만 기말 주식이 있는 주주가 있다",
                 "zero_end": "기초에는 주식이 있었지만 기말 0주(또는 빈칸)인 주주가 있다",
                 "total_mismatch": "합계 줄의 기말 주식 수가 같은 주식 종류 줄들의 합과 다르다"}
        for edge, pool in edges.items():
            for i in [x for x in rng.sample(pool, len(pool)) if x not in used][:PER_KIND // 3]:
                used.add(i)
                a = companies[i]
                add("X5", f"표의 가장자리를 묻는 문항이다. 이 회사의 2025 사업연도 최대주주 현황에는 이런 줄이 있다: {tasks[edge]}. "
                          "기말에 실제로 주식을 가진 주주가 몇 명인지, 합계 지분율이 얼마인지, 줄별 합과 합계 줄이 맞는지처럼 표 전체를 따져야 답할 수 있는 것을 묻는다. "
                          "주식 종류가 여럿이면 어느 종류 기준인지 밝힌다.",
                    a, edge=edge, largest_shareholders={y: shareholders(a, y) for y in (2024, 2025)})

        # X3: 정정으로 계약금액이 바뀐 공급계약 (정정사항 표에 계약금액 줄이 있고 앞뒤 값이 다른 것)
        changed = re.compile(r"계약금액[^|]{0,25}\| ([\d,]{9,}) \| ([\d,]{9,}) \|")
        ends = []
        for d in supply:
            if not (d.is_latest and d.is_correction and "해지" not in d.report_nm) or d.company_id in used:
                continue
            links = chain(d)
            if not 2 <= len(links) <= 4 or links[0].is_correction:
                continue
            found = [changed.search(doc_text(x.rcept_no, 1500) or "") for x in links[1:]]
            if any(m and m.group(1) != m.group(2) for m in found):
                ends.append(d)
        picked, seen = [], set()
        for d in rng.sample(ends, len(ends)):
            if d.company_id in seen or d.company_id in used:
                continue
            seen.add(d.company_id)
            picked.append(d)
            if len(picked) == PER_KIND:
                break
        for d in picked:
            used.add(d.company_id)
            add("X3", "금액을 계산하는 문항이다. 정정으로 계약금액이 바뀐 공급계약의 처음 공시부터 마지막 정정까지다. 정정 전후의 계약금액과 그 차이(늘었는지 줄었는지)를 묻거나, "
                      "조회 시점을 처음 공시와 정정 사이(또는 정정과 정정 사이)에 두고 그 시점에 공시로 알려진 금액을 묻는다. 금액은 원 단위 전체 자릿수로 쓴다.",
                companies[d.company_id], filings=[filing(x) for x in chain(d)])

        # X2: 최대주주 현황 두 사업연도 (2023과 2025, 2023이 비었으면 2024와 2025). 절반은 주식 종류가 섞인 회사
        def kinds_of(rows: list[dict]) -> set[str]:
            return {re.sub(r"\s", "", r.get("stock_knd") or "") for r in rows if not is_total(r)} - {"", "-"}

        mixed, plain = [], []
        for i in listed:
            if i in used:
                continue
            a = companies[i]
            late = shareholders(a, 2025)
            base_year = 2023 if shareholders(a, 2023) else 2024
            early = shareholders(a, base_year)
            if not early or not late:
                continue
            before, after = {norm(r["nm"]) for r in early if not is_total(r)}, {norm(r["nm"]) for r in late if not is_total(r)}
            if before == after or not 3 <= len(after) <= 30 or len(before) > 30:
                continue
            (mixed if len(kinds_of(early) | kinds_of(late)) > 1 else plain).append((i, base_year))
        for pool in (mixed, plain):
            for i, base_year in rng.sample(pool, len(pool))[:PER_KIND // 2]:
                used.add(i)
                a = companies[i]
                add("X2", f"주주 명단의 변동을 묻는 문항이다. 이 회사의 {base_year}·2025 사업연도 최대주주 현황이다. 두 명단을 견주어 새로 들어온 사람, 빠진 사람, "
                          "명단에는 남았지만 기말 0주가 된 사람, 합계 줄(주식 수·지분율)의 변화를 묻는다. 보통주와 종류주·우선주 줄이 섞여 있으면 어느 종류 기준인지 밝히게 한다.",
                    a, years=[base_year, 2025], mixed_stock_kinds=pool is mixed,
                    largest_shareholders={y: shareholders(a, y) for y in (base_year, 2025)})

    counts = defaultdict(int)
    for p in packets:
        counts[p["type"]] += 1
    print(dict(counts), "합계", len(packets))


if __name__ == "__main__":
    main()
