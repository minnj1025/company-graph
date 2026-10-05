"""공급계약 양식 파서. 실제 공시에서 확인한 양식을 줄여 만든 예시로 검사한다(원문은 저장소에 넣지 않는다)."""
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


def test_correction_reads_the_corrected_body():
    correction = ("정정신고(보고)", "정정일자", "2026-08-05", "1. 정정관련 공시서류", "단일판매ㆍ공급계약 체결",
                  "2. 정정관련 공시서류제출일", "2026-07-24", "3. 정정사유", "계약금액 정정", "4. 정정사항",
                  "정정항목", "정정전", "정정후", "2. 계약내역 / 계약금액(원)", "166,295,783,680", "199,619,617,000",
                  "- 공급 품목 추가에 따른 정정공시입니다.")
    c = parse(table(*correction, *KOSPI))
    assert c.is_correction and c.original_date == date(2026, 7, 24) and c.correction_reason == "계약금액 정정"
    assert c.changes == [("2. 계약내역 / 계약금액(원)", "166,295,783,680", "199,619,617,000")]
    assert c.amount == Decimal("199619617000")


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
