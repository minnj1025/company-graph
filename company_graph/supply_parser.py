"""단일판매ㆍ공급계약 체결 공시 원문에서 양식의 칸을 읽는다. 정해진 양식이라 규칙만 쓴다.

양식은 두 가지다 (칸 이름 다음 칸이 값).
  유가증권: 1. 판매ㆍ공급계약 구분 / - 체결계약명 / 2. 계약내역(계약금액, 최근매출액, 매출액대비) /
            3. 계약상대 / - 회사와의 관계 / 4. 판매ㆍ공급지역 / 5. 계약기간(시작일, 종료일) / 7. 계약(수주)일자
  코스닥:   1. 판매ㆍ공급계약 내용(값이 곧 계약명) / 2. 계약내역(확정·조건부 계약금액, 계약금액 총액, 최근 매출액) /
            3. 계약상대방(- 최근 매출액, - 주요사업, - 회사와의 관계) / ... / 8. 계약(수주)일자
정정 공시는 앞에 정정 표(정정일자, 원래 공시 제출일, 정정사유, 정정항목·정정전·정정후)가 붙고
그 뒤에 정정된 내용으로 양식 전체가 다시 나온다. 그래서 본문은 마지막 "1. 판매ㆍ공급계약 구분"부터 읽는다.
"""
import html
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
    body_stale: bool = False                 # 본문이 정정 전 값 그대로라 머리 표의 정정 후 금액을 썼다

    @property
    def party_hidden(self) -> bool:
        return not self.party or self.party == "-" or bool(_HIDDEN.search(self.party))


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("euc-kr", "replace")


def cells(raw: bytes) -> list[str]:
    text = _STYLE.sub("", _decode(raw)).replace("&amp;", "&").replace("&nbsp;", " ")
    return [c for c in (re.sub(r"\s+", " ", part).strip() for part in _TAG.split(text)) if c]


_TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t\1>", re.S | re.I)
_BR = re.compile(r"<br\b[^>]*>", re.I)
_SECTION = re.compile(r"^\d+\.\s*\S")


def _grid(table: str) -> list[list[tuple[int, tuple[str, ...]] | None]]:
    """표를 칸 격자로 편다. 여러 줄·여러 칸에 걸친 칸(rowspan·colspan)은 걸친 자리마다 같은 칸을 둔다.
    칸은 (번호, 줄 목록). 칸 안의 <br>로 나뉜 줄을 따로 둔다."""
    rows, carry, number = [], {}, 0
    for tr in _ROW.findall(table):
        row = {}
        for col, (left, cell) in list(carry.items()):
            row[col] = cell
            if left == 1:
                del carry[col]
            else:
                carry[col] = (left - 1, cell)
        col = 0
        for _, attrs, body in _CELL.findall(tr):
            while col in row:
                col += 1
            lines = [html.unescape(re.sub(r"\s+", " ", _TAG.sub("", line))).strip() for line in _BR.split(body)]
            cell = (number := number + 1, tuple(line for line in lines if line))
            span = {k.lower(): int(v) for k, v in re.findall(r"(rowspan|colspan)\s*=\s*\"?(\d+)", attrs, re.I)}
            for k in range(max(span.get("colspan", 1), 1)):
                row[col + k] = cell
                if span.get("rowspan", 1) > 1:
                    carry[col + k] = (span["rowspan"] - 1, cell)
            col += max(span.get("colspan", 1), 1)
        rows.append([row.get(i) for i in range(max(row) + 1)] if row else [])
    return rows


def correction_rows(raw: bytes) -> list[tuple[str, str, str]]:
    """정정 공시 머리의 정정 표(정정항목 / 정정전 / 정정후)를 (항목, 정정 전, 정정 후) 줄로 읽는다.

    표를 줄(tr)과 칸(td)으로 읽는다. 글자만 이어 붙여 세 칸씩 끊으면, 항목 칸이 "2. 계약내역<br>- 계약금액"처럼
    두 줄일 때 칸이 하나씩 밀려 정정 전·후가 어긋난다. 항목 칸의 여러 줄은 한 항목으로 잇는다.
    한 줄(tr)에 항목 여럿을 줄바꿈으로 적은 표도 있다(항목 "2. 계약내역 / - 확정 계약금액 / - 계약금액 총액",
    정정 전·후도 두 줄씩). 이때는 줄마다 한 항목으로 나누고 맨 앞 줄(구분 제목)을 각 항목 앞에 붙인다.
    """
    text = _STYLE.sub("", _decode(raw))
    for table in _TABLE.findall(text):
        grid = _grid(table)
        compact = [[re.sub(r"\s", "", " ".join(cell[1])) if cell else "" for cell in row] for row in grid]
        header = next((r for r, row in enumerate(compact) if "정정후" in row and "정정전" in row), None)
        if header is None:
            continue
        before_col, after_col = compact[header].index("정정전"), compact[header].index("정정후")
        pairs = []
        for row in grid[header + 1:]:
            if len(row) <= after_col or row[before_col] is None or row[after_col] is None:
                continue
            item_cells = list(dict.fromkeys(cell for cell in row[:before_col] if cell))
            if row[before_col] in item_cells or row[after_col] == row[before_col]:
                # 표 너비 전체에 걸친 줄: 구분 제목이면 아래에서 다음 줄들의 앞에 붙고, 설명 문장이면 넘어간다
                title = " ".join(row[after_col][1])
                if _SECTION.match(title):
                    pairs.append((title, "", ""))
                continue
            item = [line for cell in item_cells for line in cell[1]]
            before, after = list(row[before_col][1]), list(row[after_col][1])
            n = len(before)
            if n > 1 and len(after) == n and len(item) in (n, n + 1):
                head = item[:len(item) - n]
                pairs += [(" ".join(head + [label]), b, a) for label, b, a in zip(item[len(item) - n:], before, after)]
            else:
                pairs.append((" ".join(item), " ".join(before), " ".join(after)))
        return _with_sections(pairs)
    return []


def _with_sections(pairs: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """구분 제목만 있는 줄("2. 계약내역 | - | -", 또는 세 칸이 모두 "5. 계약기간")은 빼고, 아래 번호 없는 항목 앞에 붙인다.
    항목 칸이 비거나 "-"인 줄은 바로 위 항목이 이어진 것이다."""
    def blank(text: str) -> bool:
        return text in ("", "-")

    out, section = [], None
    for label, before, after in pairs:
        if _SECTION.match(label) and (label == before == after or (blank(before) and blank(after))):
            section = label
            continue
        if blank(label) and blank(before) and blank(after):
            continue
        if _SECTION.match(label):
            section = None
        elif blank(label) and out:
            label = out[-1][0]
        elif section:
            label = f"{section} {label}" if label != "-" else section
        out.append((label, before or "-", after or "-"))
    return out
    return []


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
        c.changes = correction_rows(raw)
        # 정정 공시가 머리 표(정정 전·후)만 고치고 본문은 예전 값 그대로 두는 일이 있다. 본문 금액이 "정정 전" 값이면 "정정 후" 값을 쓴다
        for label, before, after in c.changes:
            if "계약금액" in label and c.amount is not None and to_decimal(before) == c.amount and to_decimal(after) not in (None, c.amount):
                c.amount = to_decimal(after)
                c.body_stale = True
                ratio = next((to_decimal(a) for l, b, a in c.changes if "매출액" in l and "대비" in l and to_decimal(b) == c.ratio), None)
                if ratio is not None:
                    c.ratio = ratio
                break
    return c


@dataclass
class Termination:
    """단일판매ㆍ공급계약 해지 공시. 유가증권 양식은 "- 해지계약명", 코스닥 양식은 "1. 판매ㆍ공급계약 해지 내용"에 계약명이 온다."""
    title: str | None = None
    amount: Decimal | None = None          # 해지금액(원)
    party: str | None = None
    termination_date: date | None = None
    reason: str | None = None
    recent_sales: Decimal | None = None
    ratio: Decimal | None = None           # 매출액대비(%)
    party_relation: str | None = None
    period_start: date | None = None       # 해지된 계약의 계약기간
    period_end: date | None = None
    subsidiary: str | None = None
    related_dates: list[date] = field(default_factory=list)   # "관련공시" 칸에 적힌 원래 공시·정정 공시의 날짜


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
        reason=_value(cs, r"^\d+\.\s*(해지\s*주요사유|주요\s*해지사유)", body, free_text=True),
        recent_sales=to_decimal(_value(cs, r"^최근\s*매출액", body)),
        ratio=to_decimal(_value(cs, r"^매출액\s*대비", body)),
        party_relation=_value(cs, r"^-\s*회사와의 관계", body),
        period_start=to_date(_value(cs, r"^시작일$", body)),
        period_end=to_date(_value(cs, r"^종료일$", body)),
    )
    for i in range(min(body, len(cs) - 1)):
        if cs[i] in ("자회사인", "종속회사인"):
            t.subsidiary = cs[i + 1]
    related = next((i for i in range(body, len(cs)) if "관련공시" in cs[i]), None)
    if related is not None:
        for found in re.finditer(r"(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", " ".join(cs[related:related + 8])):
            try:
                t.related_dates.append(date(*map(int, found.groups())))
            except ValueError:
                pass
    return t
