"""단일판매ㆍ공급계약 체결 공시 원문에서 양식의 칸을 읽는다. 정해진 양식이라 규칙만 쓴다.

양식은 두 가지다 (칸 이름 다음 칸이 값).
  유가증권: 1. 판매ㆍ공급계약 구분 / - 체결계약명 / 2. 계약내역(계약금액, 최근매출액, 매출액대비) /
            3. 계약상대 / - 회사와의 관계 / 4. 판매ㆍ공급지역 / 5. 계약기간(시작일, 종료일) / 7. 계약(수주)일자
  코스닥:   1. 판매ㆍ공급계약 내용(값이 곧 계약명) / 2. 계약내역(확정·조건부 계약금액, 계약금액 총액, 최근 매출액) /
            3. 계약상대방(- 최근 매출액, - 주요사업, - 회사와의 관계) / ... / 8. 계약(수주)일자
정정 공시는 앞에 정정 표(정정일자, 원래 공시 제출일, 정정사유, 정정항목·정정전·정정후)가 붙고
그 뒤에 정정된 내용으로 양식 전체가 다시 나온다. 그래서 본문은 마지막 "1. 판매ㆍ공급계약 구분"부터 읽는다.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_TAG = re.compile(r"<[^>]+>")
_STYLE = re.compile(r"<style.*?</style>", re.S | re.I)
_BODY_START = re.compile(r"^1\.\s*판매\s*ㆍ\s*공급계약\s*(구분|내용)")
# 값이 비어 있으면 다음 칸 이름이 바로 온다. 그것을 값으로 읽지 않기 위한 목록
_LABEL = re.compile(
    r"^(\d+\.\s?[^\d\s]|-\s*(체결계약명|세부내용|회사와|최근|주요사업)|시작일$|종료일$|(확정|조건부)?\s*계약(금액|여부)|"
    r"최근\s*매출액|매출액\s*대비|대규모법인여부|유보사유|유보기한|계약금ㆍ선급금|대금지급|※)")
_HIDDEN = re.compile(r"유보|비공개|비밀")


@dataclass
class SupplyContract:
    kind: str | None = None
    title: str | None = None
    amount: Decimal | None = None
    recent_sales: Decimal | None = None
    ratio: Decimal | None = None
    party: str | None = None
    party_relation: str | None = None
    region: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    contract_date: date | None = None
    subsidiary: str | None = None            # "자회사의 주요경영사항"이면 실제 계약 당사자
    is_correction: bool = False
    correction_date: date | None = None
    original_date: date | None = None        # 정정 대상인 원래 공시의 제출일
    correction_reason: str | None = None
    changes: list[tuple[str, str, str]] = field(default_factory=list)   # (정정항목, 정정전, 정정후)

    @property
    def party_hidden(self) -> bool:
        return not self.party or self.party == "-" or bool(_HIDDEN.search(self.party))


def cells(raw: bytes) -> list[str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("euc-kr", "replace")
    text = _STYLE.sub("", text).replace("&amp;", "&").replace("&nbsp;", " ")
    return [c for c in (re.sub(r"\s+", " ", part).strip() for part in _TAG.split(text)) if c]


def to_decimal(text: str | None) -> Decimal | None:
    cleaned = (text or "").replace(",", "").replace("%", "").strip()
    try:
        return Decimal(cleaned) if cleaned and cleaned != "-" else None
    except InvalidOperation:
        return None


def to_date(text: str | None) -> date | None:
    # "2024-02-07", "2024.2.7", "2024년 2월 7일", 그리고 연도를 두 자리로 적은 "24년 02월 07일"
    match = re.search(r"(?<!\d)(\d{4}|\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", text or "")
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return date(year + 2000 if year < 100 else year, month, day)
    except ValueError:
        return None


def _value(cs: list[str], pattern: str, start: int = 0, stop: int | None = None, free_text: bool = False) -> str | None:
    """free_text: 값이 칸 이름처럼 생길 수 있는 칸(정정사유 "계약금액 정정" 등)은 이름 검사를 건너뛴다."""
    regex = re.compile(pattern)
    for i in range(start, (stop or len(cs)) - 1):
        if regex.search(cs[i]):
            nxt = cs[i + 1]
            return None if nxt == "-" or (not free_text and _LABEL.search(nxt)) else nxt
    return None


def parse(raw: bytes) -> SupplyContract | None:
    """양식을 찾지 못하면 None (해지 공시 등 다른 양식)."""
    cs = cells(raw)
    starts = [i for i, c in enumerate(cs) if _BODY_START.search(c)]
    if not starts:
        return None
    body = starts[-1]
    kosdaq_form = "내용" in cs[body]
    c = SupplyContract(
        kind=None if kosdaq_form else _value(cs, r"^1\.\s*판매", body),
        title=_value(cs, r"^1\.\s*판매", body) if kosdaq_form else _value(cs, r"^-\s*(체결계약명|세부내용)", body),
        # 조건부 계약이 섞이면 "계약금액 총액"이 따로 있다
        amount=to_decimal(_value(cs, r"^계약금액\s*총액", body) or _value(cs, r"^계약금액\s*(\(원\))?$", body)
                          or _value(cs, r"^확정\s*계약금액", body)),
        recent_sales=to_decimal(_value(cs, r"^최근\s*매출액", body)),
        ratio=to_decimal(_value(cs, r"^매출액\s*대비", body)),
        party=_value(cs, r"^3\.\s*계약상대(방)?$", body),
        party_relation=_value(cs, r"^-\s*회사와의 관계", body),
        region=_value(cs, r"^4\.\s*판매", body),
        period_start=to_date(_value(cs, r"^시작일$", body)),
        period_end=to_date(_value(cs, r"^종료일$", body)),
        contract_date=to_date(_value(cs, r"^\d+\.\s*계약\s*\(수주\)\s*일", body)),
    )
    for i in range(min(body, len(cs) - 1)):
        if cs[i] == "자회사인":
            c.subsidiary = cs[i + 1]
    if any(cell.startswith("정정신고") for cell in cs[:body]):
        c.is_correction = True
        c.correction_date = to_date(_value(cs, r"^정정일자", 0, body))
        c.original_date = to_date(_value(cs, r"^2\.\s*정정관련 공시서류제출일", 0, body))
        c.correction_reason = _value(cs, r"^3\.\s*정정사유", 0, body, free_text=True)
        header = next((i for i in range(body) if cs[i] == "정정후"), None)
        if header is not None:
            rows = cs[header + 1:body]
            # 표 뒤에 설명 문장과 본문 제목이 한 칸씩 붙는다. 세 칸씩 맞는 데까지만 읽는다
            while len(rows) >= 3 and not rows[0].startswith("-") and not rows[0].startswith("※"):
                c.changes.append((rows[0], rows[1], rows[2]))
                rows = rows[3:]
    return c


@dataclass
class Termination:
    """단일판매ㆍ공급계약 해지 공시. 유가증권 양식은 "- 해지계약명", 코스닥 양식은 "1. 판매ㆍ공급계약 해지 내용"에 계약명이 온다."""
    title: str | None = None
    amount: Decimal | None = None          # 해지금액(원)
    party: str | None = None
    termination_date: date | None = None
    reason: str | None = None
    subsidiary: str | None = None


def parse_termination(raw: bytes) -> Termination | None:
    cs = cells(raw)
    starts = [i for i, c in enumerate(cs) if re.search(r"^1\.\s*판매ㆍ공급계약\s*해지", c)]
    if not starts:
        return None
    body = starts[-1]
    kosdaq_form = "내용" in cs[body]
    t = Termination(
        title=_value(cs, r"^1\.\s*판매", body, free_text=True) if kosdaq_form else _value(cs, r"^-\s*해지계약명", body, free_text=True),
        amount=to_decimal(_value(cs, r"^해지금액", body)),
        party=_value(cs, r"^3\.\s*계약상대", body, free_text=True),
        termination_date=to_date(_value(cs, r"^\d+\.\s*해지일자", body)),
        reason=_value(cs, r"^\d+\.\s*해지\s*주요사유", body, free_text=True),
    )
    for i in range(min(body, len(cs) - 1)):
        if cs[i] in ("자회사인", "종속회사인"):
            t.subsidiary = cs[i + 1]
    return t
