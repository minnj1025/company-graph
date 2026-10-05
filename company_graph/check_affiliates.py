"""계열 관계 채점. 사업보고서에서 읽은 계열회사 목록을 공정위 지정 명단과 법인등록번호로 대조한다.

공정위는 매년 5월 1일 기준으로 대규모기업집단과 소속회사를 지정한다.
사업보고서의 기준일(12월 31일)과 넉 달 차이가 나므로, 그 사이의 편입·제외는 추출 오류가 아니다.
공정위가 지정하지 않은 집단(중견·중소 그룹)은 정답이 없어 채점하지 않는다.

실행: python -m company_graph.check_affiliates [연도]
"""
import sys
from collections import defaultdict

from sqlalchemy import select

from . import ftc
from .cache import cached_json
from .db import Company, Document, Relation, session


def designation_after(report_year: int) -> str:
    """사업연도 말 다음에 오는 지정(이듬해 5월)."""
    return f"{report_year + 1}05"


def main(year: int):
    ym = designation_after(year)
    rows = cached_json("ftc_affiliates", ym, lambda: ftc.affiliates(ym))
    group_of = {r["jurirno"]: r["unityGrupNm"] for r in rows}
    members: dict[str, set[str]] = defaultdict(set)
    names = {r["jurirno"]: r["entrprsNm"] for r in rows}
    for r in rows:
        members[r["unityGrupNm"]].add(r["jurirno"])

    with session() as db:
        extracted: dict[str, set[str]] = defaultdict(set)
        extracted_names: dict[str, str] = {}
        for rel in db.scalars(select(Relation).join(Document, Document.rcept_no == Relation.rcept_no).where(
                Relation.rel_type == "affiliate", Document.bsns_year == year)):
            if rel.attrs.get("jurir_no"):
                extracted[rel.rcept_no].add(rel.attrs["jurir_no"])
                extracted_names[rel.attrs["jurir_no"]] = rel.object_name_raw
        filers = {doc.rcept_no: db.get(Company, doc.company_id) for doc in db.scalars(
            select(Document).where(Document.rcept_no.in_(extracted)))}

    per_group = defaultdict(lambda: {"보고서": 0, "tp": 0, "fp": 0, "fn": 0, "only_report": set(), "only_ftc": set()})
    ungraded = 0
    for rcept_no, found in extracted.items():
        filer = filers[rcept_no]
        group = group_of.get(filer.jurir_no)
        if group is None:
            ungraded += 1
            continue
        expected = members[group] - {filer.jurir_no}
        g = per_group[group]
        g["보고서"] += 1
        g["tp"] += len(found & expected)
        g["fp"] += len(found - expected)
        g["fn"] += len(expected - found)
        g["only_report"] |= found - expected
        g["only_ftc"] |= expected - found

    print(f"== {year}년 사업보고서의 계열회사 표 ↔ 공정위 {ym[:4]}년 {int(ym[4:])}월 지정 명단 ==")
    print(f"채점한 보고서 {sum(g['보고서'] for g in per_group.values())}건 ({len(per_group)}개 집단), "
          f"공정위 지정 집단이 아니라 채점하지 않은 보고서 {ungraded}건")
    total = {k: sum(g[k] for g in per_group.values()) for k in ("tp", "fp", "fn")}
    if total["tp"]:
        print(f"전체: 정밀도 {total['tp'] / (total['tp'] + total['fp']):.1%} "
              f"(읽은 {total['tp'] + total['fp']}줄 중 {total['tp']}줄이 명단에 있음), "
              f"재현율 {total['tp'] / (total['tp'] + total['fn']):.1%} "
              f"(명단 {total['tp'] + total['fn']}줄 중 {total['tp']}줄을 읽음)")
    for group, g in sorted(per_group.items(), key=lambda kv: -kv[1]["보고서"]):
        precision = g["tp"] / (g["tp"] + g["fp"]) if g["tp"] + g["fp"] else 0
        recall = g["tp"] / (g["tp"] + g["fn"]) if g["tp"] + g["fn"] else 0
        print(f"  {group}: 보고서 {g['보고서']}건, 정밀도 {precision:.1%}, 재현율 {recall:.1%}")
        if g["only_report"]:
            print("    보고서에만 있음:", ", ".join(sorted(extracted_names[j] for j in g["only_report"])))
        if g["only_ftc"]:
            print("    공정위 명단에만 있음:", ", ".join(sorted(names[j] for j in g["only_ftc"])))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2025)
