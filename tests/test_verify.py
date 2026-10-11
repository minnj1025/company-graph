"""답의 숫자를 조회 결과와 대조하는 검증기."""
from datetime import date

from company_graph import verify

RESULT = ('{"relations": [{"value": 20.64, "detail": {"shares": "3,200,713"}, "rcept_no": ["20260312000432"], "as_of_date": "2025-12-31"}, '
          '{"value": 123456789000, "unit": "원", "disclosed_date": "2026-03-12"}]}')


def flagged(answer: str, question: str = "") -> list[str]:
    return verify.unsupported(answer, [RESULT], date(2026, 6, 30), question)


def test_values_found_in_results_pass():
    assert flagged("성도이엔지는 3,200,713주(20.64%)를 가졌습니다. 접수번호 20260312000432, 기준일 2025-12-31.") == []
    assert flagged("계약금액은 123,456,789,000원(약 1,235억 원)입니다.") == []
    assert flagged("계약금액은 1,234억 원입니다.") == [] and flagged("계약금액은 1,235억 원입니다.") == []   # 버림과 반올림 둘 다 받는다


def test_values_not_in_results_are_flagged():
    assert flagged("지분율은 20.46%입니다.") == ["20.46%"]
    assert flagged("주식 수는 3,200,731주입니다.") == ["3,200,731"]
    assert flagged("계약금액은 12,345,678,900원입니다.") == ["12,345,678,900"]
    assert flagged("근거는 20260312000999 입니다.") == ["20260312000999"]
    assert flagged("계약일은 2026-03-21 입니다.") == ["2026-03-21"]


def test_computed_values_and_question_numbers_are_left_alone():
    assert flagged("두 값의 차이는 0.64%p입니다.") == []
    assert flagged("| 회사 | 지분율 | 증감 |\n|---|---|---|\n| 가 | 20.64% | 1.20% |") == []
    assert flagged("지분율 50% 이상인 곳은 없습니다.", "지분율이 50% 이상인 출자처는?") == []
    assert flagged("2026-03-31 조회 시점까지 정정된 기록이 없습니다. B787 조립 계약입니다.", "2026-03-31 기준으로") == []
