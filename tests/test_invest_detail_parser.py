"""사업보고서 "타법인출자 현황(상세)"에서 표준 표 밖에 따로 붙인 출자 표를 읽는다."""
from datetime import date

from company_graph.invest_detail_parser import parse

HEAD = "<TR><TH>법인명</TH><TH>상장여부</TH><TH>최초취득일자</TH><TH>출자목적</TH><TH>최초취득금액</TH><TH>기초잔액</TH><TH>증가(감소)</TH><TH>기말잔액</TH><TH>최근사업연도재무현황</TH></TR><TR><TH>수량</TH><TH>지분율</TH><TH>장부가액</TH></TR>"


def row(name: str, listed: str, pct: str) -> str:
    cells = [name, listed, "2022.03", "경영참여", "100", "10", pct, "100", "-", "-", "-", "10", pct, "100", "500", "5"]
    return "<TR>" + "".join(f"<TD>{c}</TD>" for c in cells) + "</TR>"


def report(extra: str) -> bytes:
    return ("<SECTION-2><TITLE>3. 타법인출자 현황(상세)</TITLE><P>(기준일 : 2025.12.31)</P>"
            '<TABLE-GROUP ACLASS="INV_PRT"><TABLE>' + HEAD + row("(주)상장자회사", "상장", "70.7")
            + "<TR><TD>합 계</TD><TD>-</TD></TR></TABLE></TABLE-GROUP>" + extra + "</SECTION-2>").encode("utf-8")


def test_only_rows_outside_the_standard_table_are_returned():
    d = parse(report("<TABLE>" + HEAD + row("(주)비상장자회사", "비상장", "100.0%")
                     + "<TR><TD>합 계</TD><TD>-</TD></TR><TR><TD>칸이 모자란 줄</TD><TD>비상장</TD></TR></TABLE>"))
    assert (d.as_of, d.standard_rows, d.extra_tables, d.odd_rows) == (date(2025, 12, 31), 1, 1, 1)
    assert [(h.name, h.listed, h.pct) for h in d.holdings] == [("(주)비상장자회사", "비상장", "100.0%")]


def test_no_extra_table_and_no_section():
    assert parse(report("<TABLE><TR><TD>주석</TD></TR></TABLE>")).holdings == []
    assert parse("<SECTION-2><TITLE>1. 회사의 개요</TITLE></SECTION-2>".encode("utf-8")) is None
