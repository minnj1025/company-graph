"""공급계약 양식 파서. 실제 공시에서 확인한 양식을 줄여 만든 예시로 검사한다(원문은 저장소에 넣지 않는다)."""
import re
from datetime import date
from decimal import Decimal

from company_graph.supply_parser import parse


def table(*cells: str) -> bytes:
    return ("<TABLE>" + "".join(f"<TD>{c}</TD>" for c in cells) + "</TABLE>").encode("utf-8")


KOSPI = ("단일판매ㆍ공급계약 체결", "1. 판매ㆍ공급계약 구분", "상품공급", "- 체결계약명", "GPU 서버 공급 계약",
         "2. 계약내역", "계약금액(원)", "199,619,617,000", "최근매출액(원)", "4,252,067,261,000", "매출액대비(%)", "4.7",
         "대규모법인여부", "해당", "3. 계약상대", "현대자동차 주식회사", "- 회사와의 관계", "최대주주",
         "4. 판매ㆍ공급지역", "국내", "5. 계약기간", "시작일", "2026-07-20", "종료일", "2027-02-28",
         "7. 계약(수주)일자", "2026-07-24", "8. 공시유보 관련내용", "유보사유", "-")


def test_kospi_form():
    c = parse(table(*KOSPI))
    assert (c.kind, c.title, c.party, c.party_relation) == ("상품공급", "GPU 서버 공급 계약", "현대자동차 주식회사", "최대주주")
    assert (c.amount, c.recent_sales, c.ratio) == (Decimal("199619617000"), Decimal("4252067261000"), Decimal("4.7"))
    assert (c.period_start, c.period_end, c.contract_date) == (date(2026, 7, 20), date(2027, 2, 28), date(2026, 7, 24))
    assert not c.is_correction and not c.party_hidden


def correction(*rows: str, item_cols: int = 1) -> bytes:
    """정정 머리 표. rows는 "칸 || 칸 || 칸" 꼴의 줄(tr). 칸 앞의 [rowspan=2] 같은 표시는 속성으로 옮긴다.
    item_cols: 정정항목이 차지하는 칸 수 (구분 제목과 세부 항목을 따로 둔 표는 2)."""
    def td(cell: str) -> str:
        attrs, text = re.fullmatch(r"(?:\[(.*?)\]\s*)?(.*)", cell).groups()
        return f"<TD {attrs or ''}>{text}</TD>"
    head = ("<TABLE><TR><TD>정정신고(보고)</TD></TR><TR><TD>정정일자</TD><TD>2026-08-05</TD></TR></TABLE>"
            "<TABLE><TR><TD>1. 정정관련 공시서류</TD><TD colspan=2>단일판매ㆍ공급계약 체결</TD></TR>"
            "<TR><TD>2. 정정관련 공시서류제출일</TD><TD colspan=2>2026-07-24</TD></TR>"
            "<TR><TD>3. 정정사유</TD><TD colspan=2>계약금액 정정</TD></TR>"
            f"<TR><TD colspan={item_cols + 2}>4. 정정사항</TD></TR>"
            f"<TR><TD colspan={item_cols}>정정항목</TD><TD>정정전</TD><TD>정정후</TD></TR>")
    body = "".join("<TR>" + "".join(td(c) for c in row.split(" || ")) + "</TR>" for row in rows)
    return (head + body + "</TABLE>").encode("utf-8")


def test_correction_reads_the_corrected_body():
    c = parse(correction("2. 계약내역 / 계약금액(원) || 166,295,783,680 || 199,619,617,000",
                         "[colspan=3] - 공급 품목 추가에 따른 정정공시입니다.") + table(*KOSPI))
    assert c.is_correction and c.original_date == date(2026, 7, 24) and c.correction_reason == "계약금액 정정"
    assert c.changes == [("2. 계약내역 / 계약금액(원)", "166,295,783,680", "199,619,617,000")]
    assert c.amount == Decimal("199619617000")


def test_correction_item_cell_with_line_break_keeps_before_and_after_apart():
    # 항목 칸이 두 줄이면 글자를 이어 붙여 세 칸씩 끊을 때 칸이 밀려 정정 전·후가 어긋났다 (계룡건설산업 2026-06-01)
    c = parse(correction("2. 계약내역 <br> - 계약금액 || 114,802,880,000 || 113,623,285,000",
                         "2. 계약내역 <br> - 매출액대비(%) || 4.48 || 4.44",
                         "5. 계약기간 <br> - 종료일 || 2026-05-31 || 2026-07-31") + table(*KOSPI))
    assert c.changes == [("2. 계약내역 - 계약금액", "114,802,880,000", "113,623,285,000"),
                         ("2. 계약내역 - 매출액대비(%)", "4.48", "4.44"),
                         ("5. 계약기간 - 종료일", "2026-05-31", "2026-07-31")]


def test_correction_several_items_in_one_row():
    # 한 줄에 구분 제목과 항목 둘, 정정 전·후도 두 줄씩 (신원종합개발 2026-05-13)
    c = parse(correction("2. 계약내역<br>- 확정 계약금액<br>- 계약금액 총액(원) || 30,317,000,000<br>30,317,000,000 || "
                         "31,636,000,000<br>31,636,000,000") + table(*KOSPI))
    assert c.changes == [("2. 계약내역 - 확정 계약금액", "30,317,000,000", "31,636,000,000"),
                         ("2. 계약내역 - 계약금액 총액(원)", "30,317,000,000", "31,636,000,000")]


def test_correction_section_rows_and_continuations():
    c = parse(correction("[rowspan=2] 2. 계약내역 || - 계약금액 || 100 || 120",
                         "- 매출액대비(%) || 1.0 || 1.2",
                         "[colspan=4] 5. 계약기간",
                         "[colspan=2] - 종료일 || 2026-05-31 || 2026-07-31",
                         "[colspan=2] 6. 주요 계약조건 || - || -",
                         "[colspan=2] 계약금 || - || 무",
                         "[colspan=2] 9. 기타 || 가 || 나",
                         "[colspan=2] - || 다 || 라",
                         "[colspan=2] - || - || -", item_cols=2) + table(*KOSPI))
    assert c.changes == [("2. 계약내역 - 계약금액", "100", "120"), ("2. 계약내역 - 매출액대비(%)", "1.0", "1.2"),
                         ("5. 계약기간 - 종료일", "2026-05-31", "2026-07-31"), ("6. 주요 계약조건 계약금", "-", "무"),
                         ("9. 기타", "가", "나"), ("9. 기타", "다", "라")]


def test_kosdaq_form():
    c = parse(table(
        "단일판매ㆍ공급계약체결", "1. 판매ㆍ공급계약 내용", "전력변환 시스템 공급계약 체결", "2. 계약내역",
        "조건부 계약여부", "미해당", "확정 계약금액", "198,563,967,056", "조건부 계약금액", "-",
        "계약금액 총액(원)", "198,563,967,056", "최근 매출액(원)", "94,827,257,434", "매출액 대비(%)", "209.4",
        "3. 계약상대방", "현대자동차(주)", "- 최근 매출액(원)", "-", "- 주요사업", "자동차제조,판매",
        "- 회사와의 관계", "-", "5. 계약기간", "시작일", "2026-02-02", "종료일", "2033-12-31",
        "8. 계약(수주)일자", "2026-02-02"))
    assert (c.kind, c.title, c.party) == (None, "전력변환 시스템 공급계약 체결", "현대자동차(주)")
    assert (c.amount, c.recent_sales, c.ratio) == (Decimal("198563967056"), Decimal("94827257434"), Decimal("209.4"))
    assert c.party_relation is None and c.contract_date == date(2026, 2, 2)


def test_empty_value_is_not_filled_with_the_next_label():
    c = parse(table("1. 판매ㆍ공급계약 구분", "공사수주", "3. 계약상대", "- 회사와의 관계", "4. 판매ㆍ공급지역", "부산"))
    assert c.party is None and c.party_relation is None and c.region == "부산" and c.party_hidden


def test_other_forms_return_none():
    assert parse(table("단일판매ㆍ공급계약 해지", "1. 해지 내용", "계약 해지")) is None


def test_termination_forms():
    from company_graph.supply_parser import parse_termination
    kospi = parse_termination(table("단일판매ㆍ공급계약 해지", "1. 판매ㆍ공급계약 해지 구분", "공사수주", "- 해지계약명", "영종 공동주택 개발 공사",
                                    "2. 해지내역", "해지금액(원)", "396,299,951,000", "최근매출액(원)", "7,991,063,616,680",
                                    "3. 계약상대", "주식회사 영종리츠", "- 회사와의 관계", "-", "5. 해지 주요사유", "발주처와의 사업 중단 합의",
                                    "6. 해지일자", "2024-07-31"))
    assert (kospi.title, kospi.party, kospi.reason) == ("영종 공동주택 개발 공사", "주식회사 영종리츠", "발주처와의 사업 중단 합의")
    assert (kospi.amount, kospi.termination_date) == (Decimal("396299951000"), date(2024, 7, 31))
    kosdaq = parse_termination(table("1. 판매ㆍ공급계약 해지 내용", "게임 퍼블리싱 계약", "2. 해지내역", "해지금액(원)", "25,888,000,000",
                                     "3. 계약상대방", "Blockchain Game Partners, Inc.", "6. 해지일자", "2023-12-28"))
    assert (kosdaq.title, kosdaq.party, kosdaq.amount) == ("게임 퍼블리싱 계약", "Blockchain Game Partners, Inc.", Decimal("25888000000"))
    assert parse_termination(table("1. 판매ㆍ공급계약 구분", "공사수주")) is None
