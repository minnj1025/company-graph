from decimal import Decimal

from company_graph.extract_equity import unit_slip


def test_unit_slip_fixes_only_when_another_row_agrees():
    # 2026-11 상상인 2025 사업보고서: 상상인증권 60,506,763,000,000주(57%). 최대주주 현황과 2024년 표는 60,506,763주
    assert unit_slip("60,506,763,000,000", Decimal(57), {Decimal(60506763)}) == ("60,506,763", True)
    assert unit_slip("60,506,763,000,000", Decimal(57), set()) == (None, True)        # 맞는 근거가 없으면 비운다
    assert unit_slip("287,600,000", Decimal("1.9"), {Decimal(1)}) == (None, True)
    assert unit_slip("14,775,000", Decimal("37.37"), set()) == ("14,775,000", False)  # 발행주식이 100억 주 아래면 그대로
    assert unit_slip("61,530", Decimal(0), set()) == ("61,530", False)                 # 지분율 0 이면 볼 수 없다
