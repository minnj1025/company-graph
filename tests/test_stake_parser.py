"""타법인 주식 취득·처분 결정 양식 파서. 실제 공시의 양식을 줄여 만든 예시로 검사한다."""
from datetime import date
from decimal import Decimal

from company_graph.stake_parser import parse


def table(*cells: str) -> bytes:
    return ("<TABLE>" + "".join(f"<TD>{c}</TD>" for c in cells) + "</TABLE>").encode("utf-8")


ACQUISITION = ("타법인 주식 및 출자증권 취득결정", "1. 발행회사", "회사명", "Nexplus SK s.r.o.", "국적", "슬로바키아",
               "대표자", "홍길동", "자본금(원)", "10,843,846,376", "회사와 관계", "계열회사", "발행주식총수(주)", "-",
               "주요사업", "2차전지 부품", "2. 취득내역", "취득주식수(주)", "-", "취득금액(원)", "27,465,026,262",
               "자기자본(원)", "241,448,774,338", "자기자본대비(%)", "11.38", "대규모법인여부", "미해당",
               "3. 취득후 소유주식수 및 지분비율", "소유주식수(주)", "-", "지분비율(%)", "100.00",
               "4. 취득방법", "현금 취득", "5. 취득목적", "유럽 현지 생산거점 확보", "6. 취득예정일자", "2026-08-31",
               "10. 이사회결의일(결정일)", "2026-08-21", "-사외이사 참석여부", "참석(명)", "1")


def test_acquisition_form():
    d = parse(table(*ACQUISITION), "acquisition")
    assert (d.target, d.nationality, d.relation, d.business) == ("Nexplus SK s.r.o.", "슬로바키아", "계열회사", "2차전지 부품")
    assert (d.amount, d.equity, d.equity_ratio, d.pct_after) == (
        Decimal("27465026262"), Decimal("241448774338"), Decimal("11.38"), Decimal("100.00"))
    assert (d.method, d.purpose) == ("현금 취득", "유럽 현지 생산거점 확보")
    assert (d.expected_date, d.decision_date) == (date(2026, 8, 31), date(2026, 8, 21))
    assert not d.is_correction and d.subsidiary is None


def test_disposal_form_uses_the_other_verb():
    disposal = tuple(c.replace("취득", "처분") for c in ACQUISITION)
    d = parse(table(*disposal), "disposal")
    assert (d.amount, d.pct_after, d.purpose) == (Decimal("27465026262"), Decimal("100.00"), "유럽 현지 생산거점 확보")
    assert parse(table(*disposal), "acquisition").amount is None


def test_subsidiary_and_correction_headers():
    header = ("정정신고(보고)", "정정일자", "2026-09-01", "2. 정정관련 공시서류제출일", "2026-08-21", "3. 정정사유", "취득금액 정정",
              "4. 정정사항", "정정항목", "정정전", "정정후", "2. 취득내역 / 취득금액(원)", "20,000,000,000", "27,465,026,262",
              "- 정정 설명", "타법인 주식 및 출자증권 취득결정", "종속회사인", "(주)자회사", "의 주요경영사항 신고")
    d = parse(table(*header, *ACQUISITION), "acquisition")
    assert d.is_correction and d.original_date == date(2026, 8, 21) and d.correction_reason == "취득금액 정정"
    assert d.changes == [("2. 취득내역 / 취득금액(원)", "20,000,000,000", "27,465,026,262")]
    assert d.subsidiary == "(주)자회사" and d.amount == Decimal("27465026262")


def test_other_forms_return_none():
    assert parse(table("금전대여 결정", "1. 대여 상대", "자회사"), "acquisition") is None


def test_kosdaq_form_has_name_and_country_in_one_cell_split_across_lines():
    d = parse(table("타법인 주식 및 출자증권 처분결정", "1. 발행회사", "회사명(국적)", "북경세동릉운과기", "유한공사(중국)",
                    "대표이사", "나개전", "자본금(원)", "9,904,801,075", "회사와 관계", "관계기업", "주요사업", "자동차부품제조",
                    "2. 처분내역", "처분주식수(주)", "-", "처분금액(원)", "4,989,299,407", "자기자본(원)", "37,423,893,504",
                    "자기자본대비(%)", "13.33", "대기업여부", "미해당", "3. 처분후 소유주식수 및 지분비율", "소유주식수(주)", "-",
                    "지분비율(%)", "0", "4. 처분목적", "경영효율성 제고", "5. 처분예정일자", "2024-06-07",
                    "6. 이사회결의일(결정일)", "2024-05-17"), "disposal")
    assert (d.target, d.nationality, d.relation) == ("북경세동릉운과기 유한공사", "중국", "관계기업")
    assert (d.amount, d.equity_ratio, d.pct_after, d.decision_date) == (
        Decimal("4989299407"), Decimal("13.33"), Decimal("0"), date(2024, 5, 17))
