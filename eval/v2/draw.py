"""평가 문항 2판에 쓸 자료 묶음을 뽑는다. 문항과 정답은 이 묶음(원자료)만 보고 따로 쓴다.

모집단은 공시 목록(document 표)과 기업 원장이다. 묶음에 넣는 것은 추출기를 거치지 않은 원자료다:
공시 원문의 표 칸을 순서대로 이은 글, OpenDART API 응답 그대로, 사업보고서의 계열회사 표 칸.
관계 표(relation)는 "어느 공시를 고를지" 정하는 데만 쓰고, 묶음의 내용으로는 넣지 않는다.

실행: python -m eval.v2.draw   →  eval/v2/packets/*.json
"""
import html
import json
import random
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select, text

from company_graph import dart
from company_graph.db import Company, Document, session
from company_graph.supply_parser import cells

SEED = 20261009
OUT = Path(__file__).parent / "packets"
LISTED = ("Y", "K", "N")
V1 = {"012630", "178320", "439260", "412350", "012030", "272210", "147830", "375500", "234920", "016380", "016450",
      "007460", "076610", "229500", "019770", "069260", "011320", "028260", "052420", "462860", "084670", "024110",
      "219420", "003670", "064550"}   # 1판에 쓴 기업은 다시 쓰지 않는다


def doc_text(rcept_no: str, limit: int = 3800) -> str:
    """공시 원문의 표 칸을 순서대로. 추출기의 양식 해석은 쓰지 않는다."""
    return " | ".join(c[:160] for c in cells(dart.document(rcept_no)))[:limit]


def affiliate_cells(rcept_no: str, limit: int = 5000) -> str | None:
    raw = dart.document(rcept_no).decode("utf-8", "replace")
    found = re.search(r'<TABLE-GROUP[^>]*ACLASS="AFF_CMP"[^>]*>(.*?)</TABLE-GROUP>', raw, re.S)
    if not found:
        return None
    parts = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in re.findall(r"<T[DHEU]\b[^>]*>(.*?)</T[DHEU]>", found.group(1), re.S)]
    return " | ".join(p for p in parts if p)[:limit]


def main():
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    packets = []

    def add(kind: str, task: str, company: Company, **body):
        number = sum(1 for p in packets if p["type"] == kind) + 1
        packet = {"id": f"{kind}-{number:02d}", "type": kind, "task": task,
                  "company": {"name": company.name, "stock_code": company.stock_code, "group": company.ftc_group}, **body}
        packets.append(packet)
        (OUT / f"{packet['id']}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    with session() as db:
        companies = {c.company_id: c for c in db.scalars(select(Company))}
        listed = {i for i, c in companies.items() if c.corp_cls in LISTED and c.stock_code not in V1}
        docs = [d for d in db.scalars(select(Document).order_by(Document.rcept_no)) if d.company_id in listed]
        supply = [d for d in docs if d.doc_type == "supply_contract"]
        events = [d for d in docs if d.doc_type == "event"]
        annual = {(d.company_id, d.bsns_year): d for d in docs if d.doc_type == "annual"}
        by_no = {d.rcept_no: d for d in docs}
        used: set[int] = set()

        def fresh(pool, n, key=lambda d: d.company_id):
            """회사가 겹치지 않게 n개."""
            chosen = []
            for item in rng.sample(pool, len(pool)):
                if key(item) in used:
                    continue
                used.add(key(item))
                chosen.append(item)
                if len(chosen) == n:
                    break
            return chosen

        def filing(d: Document) -> dict:
            return {"rcept_no": d.rcept_no, "filed": d.rcept_dt.isoformat(), "report": d.report_nm, "cells": doc_text(d.rcept_no)}

        def chain(d: Document) -> list[Document]:
            out = [d]
            while out[0].corrects_rcept_no in by_no:
                out.insert(0, by_no[out[0].corrects_rcept_no])
            return out

        # T1·T2: 정정이 두 번 이상 이어진 공급계약
        ends = [d for d in supply if d.is_latest and d.is_correction and "해지" not in d.report_nm and len(chain(d)) >= 3]
        for d in fresh(ends, 20):
            kind = "T01" if sum(1 for p in packets if p["type"] == "T01") < 10 else "T02"
            task = ("정정이 여러 번 이어진 계약이다. 정정과 정정 사이의 한 시점을 조회 시점으로 잡아, 그 시점에 공시로 알려진 값(금액이나 종료일)을 묻는다. "
                    "나중 정정의 값을 답하면 틀리는 문항이어야 한다." if kind == "T01" else
                    "정정이 여러 번 이어진 계약이다. 특정 정정 공시가 무엇을 얼마에서 얼마로 바꿨는지, 그리고 처음 공시부터 지금까지 값이 어떻게 변해 왔는지를 묻는다.")
            add(kind, task, companies[d.company_id], filings=[filing(x) for x in chain(d)[-4:]])

        # T3: 해지
        ended = [d for d in supply if "해지" in d.report_nm and not d.is_correction]
        for d in fresh(ended, 10):
            original = by_no.get(d.corrects_rcept_no) if d.corrects_rcept_no else None
            add("T03", "해지된 계약이다. 해지 공시 뒤의 한 시점을 조회 시점으로 잡아 이 계약이 유효한지, 해지 금액과 사유가 무엇인지 묻는다. "
                       "원래 계약 공시가 묶음에 있으면 해지 전 시점의 질문으로 바꿔도 된다(그때는 유효하다가 정답).",
                companies[d.company_id], filings=([filing(x) for x in chain(original)[-2:]] if original else []) + [filing(d)])

        # T4: 정정되거나 철회된 지분 취득·처분 결정
        revised = [d for d in events if d.is_correction and d.is_latest and d.corrects_rcept_no in by_no]
        for d in fresh(revised, 8):
            add("T04", "정정(또는 철회)된 타법인 주식 취득·처분 결정이다. 정정 전후에 무엇이 달라졌는지, 또는 정정 전 시점에 알려져 있던 값이 무엇인지 묻는다.",
                companies[d.company_id], filings=[filing(x) for x in chain(d)[-3:]])

        # T5: 자회사의 주요경영사항 (공시한 회사와 실제 당사자가 다르다)
        via_parent = [d for d in supply + events if re.search(r"자회사|종속회사", d.report_nm) and not d.is_correction]
        for d in fresh(via_parent, 10):
            add("T05", "모회사가 자회사(종속회사)의 일을 대신 공시한 것이다. 실제로 계약을 맺거나 결정을 한 회사가 어디인지와 그 내용(상대, 금액)을 함께 묻는다.",
                companies[d.company_id], filings=[filing(d)])

        # T6: 한 회사가 한 해에 새로 낸 공급계약 전부
        yearly: dict[tuple, list[Document]] = defaultdict(list)
        for d in supply:
            if d.rcept_dt.year in (2024, 2025) and "해지" not in d.report_nm:
                yearly[(d.company_id, d.rcept_dt.year)].append(d)
        several = [k for k, ds in yearly.items() if 4 <= sum(1 for x in ds if not x.is_correction) <= 7 and any(x.is_correction for x in ds)]
        for key in fresh(sorted(several), 10, key=lambda k: k[0]):
            add("T06", f"이 회사가 {key[1]}년에 낸 공급계약 공시 전부다(정정 공시 포함). 정정 공시를 빼고 새로 체결을 공시한 계약만 빠짐없이 들게 하거나, "
                       "그해 공시한 계약 금액의 합처럼 전부를 봐야 답할 수 있는 것을 묻는다.",
                companies[key[0]], year=key[1], filings=[filing(x) | {"cells": doc_text(x.rcept_no, 1500)} for x in yearly[key]])

        # T7: 한 회사를 상대로 공급계약을 공시한 회사 전부 (원문에서 상대 이름을 글자로 찾는다)
        targets = [("005930", "삼성전자"), ("000660", "에스케이하이닉스|SK하이닉스"), ("373220", "엘지에너지솔루션|LG에너지솔루션"),
                   ("015760", "한국전력공사"), ("009540", "HD한국조선해양|한국조선해양"), ("034020", "두산에너빌리티"),
                   ("047810", "한국항공우주산업|한국항공우주"), ("012450", "한화에어로스페이스")]
        quarters = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"), ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"),
                    ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30")]
        for code, pattern in targets:
            start, end = rng.choice(quarters)
            hits = []
            for d in supply:
                if start <= d.rcept_dt.isoformat() <= end and "해지" not in d.report_nm:
                    body = doc_text(d.rcept_no, 2600)
                    party = re.search(r"3\. 계약상대(?:방)? \| ([^|]+)", body)
                    if party and re.search(pattern, party.group(1).replace(" ", "")):
                        hits.append(filing(d) | {"cells": body, "filer": companies[d.company_id].name})
            target = next(c for c in companies.values() if c.stock_code == code)
            add("T07", f"{start}~{end}에 이 회사를 계약상대로 적은 공급계약 공시 전부다(상대 칸에 이름이 글자로 들어간 공시를 원문에서 찾았다). "
                       "그 기간에 이 회사를 상대로 새 계약을 공시한 회사를 빠짐없이 들게 한다. 정정 공시만 낸 회사는 따로 구분해야 한다.",
                target, period=[start, end], filings=hits[:25], filings_found=len(hits))

        # T8: 상대를 밝히지 않은 계약, 공시가 없는 회사
        hidden = [by_no[r] for r in db.scalars(text(
            "select rcept_no from relation where rel_type='supply_contract' and retired_at is null and object_name_raw='-'")) if r in by_no]
        for d in fresh([d for d in hidden if not d.is_correction], 5):
            add("T08", "계약상대를 밝히지 않은 공급계약이다. 상대가 누구인지를 묻되, 원문에 실마리(지역, 업종, 주석)가 있어도 단정하면 안 되는 문항으로 만든다.",
                companies[d.company_id], filings=[filing(d)])
        filers = {d.company_id for d in supply}
        for company_id in fresh(sorted(listed - filers), 5, key=lambda i: i):
            add("T08", "이 회사는 2024년 1월 이후 단일판매ㆍ공급계약 공시를 한 건도 내지 않았다(공시 목록 기준). 특정 기간에 낸 공급계약을 묻고, "
                       "\"모른다\"가 아니라 \"공시된 것이 없다\"가 정답이 되게 한다.", companies[company_id], filings=[])

        # T9: 지분 취득·처분 결정의 세부
        plain = [d for d in events if not d.is_correction and d.is_latest and "철회" not in d.report_nm]
        for d in fresh(plain, 10):
            add("T09", "타법인 주식 취득·처분 결정이다. 대상과 금액만 묻지 말고, 취득(처분) 뒤 지분율, 방법, 목적, 자기자본 대비 비율 가운데 둘 이상을 함께 묻는다.",
                companies[d.company_id], filings=[filing(d)])

        # T10~T14: 사업보고서 (최대주주, 출자, 계열)
        def annual_packet(company: Company, years=(2023, 2025)) -> dict:
            report = annual.get((company.company_id, 2025))
            return {"largest_shareholders": {y: dart.largest_shareholders(company.corp_code, y) for y in years},
                    "other_corp_investments_2025": dart.other_corp_investments(company.corp_code, 2025),
                    "annual_report_2025": report and report.rcept_no,
                    "affiliate_table_2025_cells": affiliate_cells(report.rcept_no) if report else None}

        with_reports = [companies[i] for i in sorted(listed) if (i, 2025) in annual and (i, 2023) in annual and companies[i].corp_code]
        specs = [("T10", "최대주주와 특수관계인이다. 최대주주 본인과 특수관계인을 구분해 묻는다(예: 최대주주 본인의 지분율과, 특수관계인을 합친 지분율). 보통주 줄만 쓴다."),
                 ("T11", "2023년 말과 2025년 말의 최대주주 현황이다. 최대주주가 바뀌었는지, 지분율이 얼마에서 얼마로 변했는지를 묻는다. 조회 시점에 따라 답이 달라지게 두 시점 중 하나를 고르거나 둘을 비교하게 한다."),
                 ("T12", "타법인 출자현황이다. 이 회사가 지분을 가진 회사 가운데 조건에 맞는 것을 모두 들게 한다(예: 지분율 20% 이상, 경영참여 목적). 조건은 이 자료로 판정할 수 있는 것만 쓴다."),
                 ("T13", "계열회사 표다(상장·비상장 구분과 회사 수가 적혀 있다). 계열회사 가운데 상장사를 모두 들게 하거나, 상장·비상장 수를 묻는다. 표가 없으면 계열회사가 없다는 것이 정답이다.")]
        for kind, task in specs:
            for company in fresh(with_reports, 10, key=lambda c: c.company_id):
                add(kind, task, company, **annual_packet(company))

        # T14: 두 단계 (최대주주가 상장사인 회사)
        names = {c.name: c for c in companies.values() if c.corp_cls in LISTED and c.corp_code}
        two_step = []
        for company in rng.sample(with_reports, len(with_reports)):
            rows = dart.largest_shareholders(company.corp_code, 2025)
            top = max((r for r in rows if r.get("nm") not in ("계", "합계")), key=lambda r: float((r.get("trmend_posesn_stock_qota_rt") or "0").replace(",", "") or 0), default=None)
            holder = top and next((c for n, c in names.items() if re.sub(r"\(주\)|㈜|주식회사|\s", "", n) == re.sub(r"\(주\)|㈜|주식회사|\s", "", top["nm"])), None)
            if holder and holder.company_id != company.company_id and company.company_id not in used:
                used.add(company.company_id)
                two_step.append((company, holder))
            if len(two_step) == 10:
                break
        for company, holder in two_step:
            add("T14", "두 단계 질문이다. 이 회사의 최대주주가 누구인지 찾고, 그 최대주주(회사)의 최대주주가 누구인지, 또는 그 최대주주가 지분을 가진 다른 회사를 묻는다.",
                company, **annual_packet(company, years=(2025,)),
                largest_shareholder={"name": holder.name, "stock_code": holder.stock_code, **annual_packet(holder, years=(2025,))})

        # T15: 계열회사를 상대로 한 공급계약 (원문의 "회사와의 관계" 칸)
        related: dict[tuple, list[Document]] = defaultdict(list)
        for key, ds in yearly.items():
            if key[0] in used or not 2 <= len(ds) <= 8:
                continue
            if any(re.search(r"회사와의 관계 \| (계열회사|최대주주|자회사|관계회사|종속회사|특수관계)", doc_text(x.rcept_no, 1500)) for x in ds):
                related[key] = ds
        for key in fresh(sorted(related), 8, key=lambda k: k[0]):
            add("T15", f"이 회사가 {key[1]}년에 낸 공급계약 공시 전부다. \"회사와의 관계\" 칸을 보고, 계열회사나 최대주주 같은 특수관계자를 상대로 한 계약만 골라 들게 한다.",
                companies[key[0]], year=key[1], filings=[filing(x) | {"cells": doc_text(x.rcept_no, 1500)} for x in related[key]])

    counts = defaultdict(int)
    for p in packets:
        counts[p["type"]] += 1
    print(dict(counts), "합계", len(packets))


if __name__ == "__main__":
    main()
