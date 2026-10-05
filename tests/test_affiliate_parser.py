"""계열회사 현황 표 파서. 실제 사업보고서의 표 구조를 줄여 만든 예시로 검사한다."""
from datetime import date

from company_graph.affiliate_parser import parse

DOC = """
<TITLE>2. 계열회사 현황(상세)</TITLE>
<TABLE-GROUP ACLASS="AFF_CMP" ADELETETABLE="N">
<TABLE><TBODY><TR><TD>(기준일 : </TD><TU AUNIT="BASE_DT" AUNITVALUE="20251231">2025년 12월 31일</TU><TD>)</TD><TD>(단위 : 사)</TD></TR></TBODY></TABLE>
<TABLE><THEAD><TR><TH>상장여부</TH><TH>회사수</TH><TH>기업명</TH><TH>법인등록번호</TH></TR></THEAD>
<TBODY>
<TR><TE ROWSPAN="2">상장</TE><TE ROWSPAN="2">2</TE><TE>현대자동차</TE><TE>110111-0085450</TE></TR>
<TR><TE>기아</TE><TE>110111-0037998</TE></TR>
<TR><TE ROWSPAN="2">비상장</TE><TE ROWSPAN="2">2</TE><TE>현대케피코</TE><TE>110111-0543169</TE></TR>
<TR><TE>Hyundai Motor America</TE><TE>-</TE></TR>
</TBODY></TABLE>
</TABLE-GROUP>
<TITLE>3. 타법인출자 현황(상세)</TITLE>
"""


def test_reads_names_numbers_and_listing():
    table = parse(DOC.encode("utf-8"))
    assert table.as_of == date(2025, 12, 31)
    assert [(a.name, a.jurir_no, a.listed) for a in table.affiliates] == [
        ("현대자동차", "1101110085450", True), ("기아", "1101110037998", True),
        ("현대케피코", "1101110543169", False), ("Hyundai Motor America", None, False)]


def test_declared_count_is_checked():
    table = parse(DOC.encode("utf-8"))
    assert table.declared_count == 4 and table.count_matches
    missing_row = DOC.replace("<TR><TE>기아</TE><TE>110111-0037998</TE></TR>", "")
    assert parse(missing_row.encode("utf-8")).count_matches is False


def test_no_table_returns_none():
    assert parse("<TITLE>2. 계열회사 현황(상세)</TITLE><P>해당사항 없음</P>".encode("utf-8")) is None
