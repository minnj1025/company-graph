"""작은 표본과 100%에 가까운 비율을 정직하게 보고하기 위한 계산."""
from decimal import Decimal
from math import sqrt


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """비율의 95% Wilson 구간. 흔한 '평균 ± 1.96×표준오차'는 표본이 작거나 비율이 100%에 가까우면 너무 좁게 나온다."""
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    centre = p + z * z / (2 * total)
    margin = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    denominator = 1 + z * z / total
    return max(0.0, (centre - margin) / denominator), min(1.0, (centre + margin) / denominator)


def rate(successes: int, total: int) -> str:
    low, high = wilson(successes, total)
    return f"{successes}/{total} = {successes / total:.1%} (95% 구간 {low:.1%}~{high:.1%})" if total else "0/0"


def written_interval(text: str, trailing_zeros_count: bool = True) -> tuple[Decimal, Decimal]:
    """공시에 적힌 숫자가 뜻하는 구간. '6.87'은 반올림했다면 [6.865, 6.875), 버렸다면 [6.87, 6.88)이므로
    둘을 합친 [6.865, 6.88)을 돌려준다 (XBRL Calculations 1.1이 반올림된 값을 구간으로 보는 것과 같은 생각).

    trailing_zeros_count=False 이면 끝의 0을 자릿수로 치지 않는다. '41.10'을 소수 첫째 자리까지만 믿고
    [41.05, 41.2)로 본다. 표의 자릿수를 맞추려고 0을 채워 적는 회사가 있어서다.
    """
    value = Decimal(text.replace(",", "").strip())
    exponent = value.as_tuple().exponent if trailing_zeros_count else min(0, value.normalize().as_tuple().exponent)
    unit = Decimal(1).scaleb(exponent)
    return value - unit / 2, value + unit


def written_values_agree(a: str, b: str, trailing_zeros_count: bool = True) -> bool:
    """두 공시에 적힌 숫자가 같은 값을 가리킬 수 있는가 = 두 구간이 겹치는가."""
    (a_low, a_high), (b_low, b_high) = (written_interval(x, trailing_zeros_count) for x in (a, b))
    return a_low < b_high and b_low < a_high
