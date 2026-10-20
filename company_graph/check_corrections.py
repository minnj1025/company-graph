"""정정 내역 점검. 정정 공시의 정정 표를 읽은 결과(changes)를 캐시의 원문 표와 다른 방법으로 대조한다.
공급계약(단일판매ㆍ공급계약)과 지분 결정(타법인 주식 취득·처분결정)이 같은 정정 표를 쓴다.

표의 줄(tr)마다 마지막 두 칸이 정정 전·정정 후다(구분 제목 칸이 여러 줄에 걸쳐도 마지막 두 칸은 그대로다).
읽은 줄의 (정정 전, 정정 후)가 원문의 한 줄 안에서 그 두 칸에 나란히 들어 있으면 맞게 읽은 것이다.
정정 전·후가 뒤바뀌거나 칸이 밀려 읽혔으면 이 대조에서 걸린다.
거꾸로 원문에서 값이 있는 줄을 하나도 읽지 않았으면 빠뜨린 것이다.

실행: python -m company_graph.check_corrections            캐시에 있는 공급계약·지분 결정 정정 공시 전부
      python -m company_graph.check_corrections 접수번호...  그 공시의 원문 표와 읽은 결과를 나란히 보인다
"""
import html
import re
import sys
from collections import Counter, defaultdict

from sqlalchemy import select

from . import stake_parser, supply_parser
from .config import CACHE_DIR
from .db import Document, session
from .supply_parser import _BR, _ROW, _TABLE, _TAG, _decode

_STAKE = {"타법인주식및출자증권취득결정": "acquisition", "타법인주식및출자증권처분결정": "disposal"}


def source_rows(raw: bytes) -> list[list[str]]:
    """정정 표의 줄마다 칸의 글자(칸 안의 줄바꿈은 띄어쓰기로). 머리 줄(정정항목·정정전·정정후) 다음부터."""
    for table in _TABLE.findall(_decode(raw)):
        rows = [[" ".join(html.unescape(re.sub(r"\s+", " ", _TAG.sub("", line))).strip() for line in _BR.split(body)).strip()
                 for body in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
                for tr in _ROW.findall(table)]
        header = next((i for i, row in enumerate(rows)
                       if [re.sub(r"\s", "", c) for c in row[-2:]] == ["정정전", "정정후"]), None)
        if header is not None:
            return [[re.sub(r"\s+", " ", c) for c in row] for row in rows[header + 1:]]
    return []


def kind_of(report_nm: str) -> str:
    """공시 이름으로 양식을 가린다: "supply", "acquisition", "disposal"."""
    name = re.sub(r"^\[[^\]]+\]", "", re.sub(r"\s+", "", report_nm))
    return next((action for prefix, action in _STAKE.items() if name.startswith(prefix)), "supply")


def parsed(raw: bytes, kind: str):
    return supply_parser.parse(raw) if kind == "supply" else stake_parser.parse(raw, kind)


def problems(raw: bytes, changes: list[tuple[str, str, str]]) -> list[str]:
    source = [row for row in source_rows(raw) if len(row) >= 2]
    found = []
    for label, before, after in changes:
        if not any(before in row[-2] and after in row[-1] for row in source):
            found.append(f"원문 한 줄에 나란히 없음: {label} | {before} | {after}")
    for row in source:
        blank = all(c in ("", "-") for c in row[-2:]) or row[-2] == row[-1] == row[0]
        if not blank and not any(b in row[-2] and a in row[-1] for _, b, a in changes):
            found.append(f"원문 줄을 읽지 않음: {' | '.join(row)}")
    return found


def show(rcept_no: str):
    raw = (CACHE_DIR / "dart_document" / f"{rcept_no}.xml").read_bytes()
    with session() as db:
        doc = db.get(Document, rcept_no)
    decision = parsed(raw, kind_of(doc.report_nm) if doc else "supply")
    changes = decision.changes if decision else []
    print(f"== {rcept_no} {doc.report_nm if doc else ''}\n원문 표:")
    for row in source_rows(raw):
        print("   ", " || ".join(row))
    print("읽은 결과 (항목 | 정정 전 | 정정 후):")
    for row in changes:
        print("   ", " | ".join(row))
    for p in problems(raw, changes):
        print("  !", p)


def main():
    with session() as db:
        docs = db.execute(select(Document.rcept_no, Document.report_nm).where(
            Document.doc_type.in_(("supply_contract", "event")), Document.is_correction.is_(True))).all()
    stats, failed = defaultdict(Counter), []
    for rcept_no, report_nm in sorted(docs):
        kind = kind_of(report_nm)
        if kind == "supply" and "단일판매" not in report_nm:
            continue
        group = "공급계약" if kind == "supply" else "지분 결정"
        path = CACHE_DIR / "dart_document" / f"{rcept_no}.xml"
        if not path.exists():
            stats[group]["캐시에 원문 없음"] += 1
            continue
        raw = path.read_bytes()
        decision = parsed(raw, kind)
        if decision is None or not decision.is_correction:
            stats[group]["정정 양식이 아님(해지·철회 등)"] += 1
            continue
        stats[group]["정정 공시"] += 1
        stats[group]["읽은 정정 줄"] += len(decision.changes)
        if not decision.changes:
            stats[group]["정정 줄이 없음"] += 1
        found = problems(raw, decision.changes)
        if found:
            stats[group]["원문과 어긋남"] += 1
            failed.append((rcept_no, found))
    for group, counts in stats.items():
        print(f"[{group}]")
        for key, value in counts.items():
            print(f"  {key}: {value:,}")
    for rcept_no, found in failed[:30]:
        print(rcept_no)
        for p in found[:3]:
            print("   ", p[:200])


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        show(arg)
    if len(sys.argv) == 1:
        main()
