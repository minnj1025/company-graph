"""타법인 주식 및 출자증권 취득결정 / 처분결정 공시의 양식을 읽는다. 정해진 양식이라 규칙만 쓴다.

양식은 두 가지다 (칸 이름 다음 칸이 값).
  유가증권: 1. 발행회사(회사명, 국적, 대표자, 회사와 관계, 주요사업) / 2. 취득내역(취득금액, 자기자본, 자기자본대비) /
            3. 취득후 소유주식수 및 지분비율 / 4. 취득방법 / 5. 취득목적 / 6. 취득예정일자 / 10. 이사회결의일(결정일)
  코스닥:   회사명과 국적이 "회사명(국적)" 한 칸에 "북경○○유한공사(중국)"처럼 적히고, 대표자가 "대표이사"다.
            이름이 길면 줄이 바뀌어 여러 칸으로 쪼개진다
처분결정은 "취득"이 "처분"으로 바뀐 같은 구조다. 정정 공시와 자회사 공시의 머리말은 공급계약 공시와 같다.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .supply_parser import cells, to_date, to_decimal

_BODY_START = re.compile(r"^1\.\s*발행회사")
_LABEL = re.compile(r"^(\d+\.\s?[^\d\s]|-\s*\S|회사명(\(국적\))?$|국적$|대표(자|이사)$|자본금|회사와\s*관계|발행주식총수|"
                    r"주요사업$|(취득|처분)(주식수|금액)|자기자본|대(규모법인|기업)여부|소유주식수|지분비율|※)")
_TRAILING_COUNTRY = re.compile(r"\s*\(([^()]{1,20})\)\s*$")


@dataclass
class StakeDecision:
    action: str                               # "acquisition" 또는 "disposal"
    target: str | None = None                 # 발행회사 = 지분을 사거나 파는 대상
    nationality: str | None = None
    relation: str | None = None               # 회사와 관계 (계열회사, 자회사 등)
    business: str | None = None
    amount: Decimal | None = None             # 취득·처분 금액(원)
    equity: Decimal | None = None             # 자기자본(원)
    equity_ratio: Decimal | None = None       # 자기자본대비(%)
    pct_after: Decimal | None = None          # 거래 후 지분비율(%)
    method: str | None = None
    purpose: str | None = None
    expected_date: date | None = None
    decision_date: date | None = None         # 이사회결의일(결정일)
    subsidiary: str | None = None
    is_correction: bool = False
    original_date: date | None = None
    correction_reason: str | None = None
    changes: list[tuple[str, str, str]] = field(default_factory=list)


def _value(cs: list[str], pattern: str, start: int, stop: int | None = None, free_text: bool = False) -> str | None:
    regex = re.compile(pattern)
    for i in range(start, (stop or len(cs)) - 1):
        if regex.search(cs[i]):
            nxt = cs[i + 1]
            return None if nxt == "-" or (not free_text and _LABEL.search(nxt)) else nxt
    return None


def _target(cs: list[str], body: int) -> tuple[str | None, str | None]:
    """발행회사의 이름과 국적. 이름은 다음 칸 이름(대표자)이 나올 때까지 이어 붙인다."""
    for i in range(body, len(cs) - 1):
        if re.match(r"^회사명(\(국적\))?$", cs[i]):
            parts = []
            for cell in cs[i + 1:i + 5]:
                if _LABEL.search(cell):
                    break
                parts.append(cell)
            name = " ".join(parts).strip()
            if not name or name == "-":
                return None, None
            if "국적" in cs[i]:
                country = _TRAILING_COUNTRY.search(name)
                if country:
                    return _TRAILING_COUNTRY.sub("", name).strip(), country.group(1)
            return name, None
    return None, None


def parse(raw: bytes, action: str) -> StakeDecision | None:
    """양식을 찾지 못하면 None."""
    cs = cells(raw)
    starts = [i for i, c in enumerate(cs) if _BODY_START.search(c)]
    if not starts:
        return None
    body = starts[-1]
    verb = "취득" if action == "acquisition" else "처분"
    target, country = _target(cs, body)
    d = StakeDecision(
        action=action,
        target=target,
        nationality=country or _value(cs, r"^국적$", body),
        relation=_value(cs, r"^회사와\s*관계$", body),
        business=_value(cs, r"^주요사업$", body),
        amount=to_decimal(_value(cs, rf"^{verb}금액\s*\(원\)", body)),
        equity=to_decimal(_value(cs, r"^자기자본\s*\(원\)", body)),
        equity_ratio=to_decimal(_value(cs, r"^자기자본\s*대비", body)),
        pct_after=to_decimal(_value(cs, r"^지분비율\s*\(%\)", body)),
        method=_value(cs, rf"^\d+\.\s*{verb}방법", body, free_text=True),
        purpose=_value(cs, rf"^\d+\.\s*{verb}목적", body, free_text=True),
        expected_date=to_date(_value(cs, rf"^\d+\.\s*{verb}예정일자", body)),
        decision_date=to_date(_value(cs, r"^\d+\.\s*이사회결의일", body)),
    )
    for i in range(min(body, len(cs) - 1)):
        if cs[i] in ("자회사인", "종속회사인"):
            d.subsidiary = cs[i + 1]
    if any(cell.startswith("정정신고") for cell in cs[:body]):
        d.is_correction = True
        d.original_date = to_date(_value(cs, r"^2\.\s*정정관련 공시서류제출일", 0, body, free_text=True))
        d.correction_reason = _value(cs, r"^3\.\s*정정사유", 0, body, free_text=True)
        header = next((i for i in range(body) if cs[i] == "정정후"), None)
        if header is not None:
            rows = cs[header + 1:body]
            while len(rows) >= 3 and not rows[0].startswith("-") and not rows[0].startswith("※"):
                d.changes.append((rows[0], rows[1], rows[2]))
                rows = rows[3:]
    return d
