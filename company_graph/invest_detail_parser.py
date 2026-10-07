"""사업보고서 본문 "타법인출자 현황(상세)"에서, 표준 표 밖에 회사가 따로 붙인 출자 표를 읽는다.

OpenDART의 타법인 출자현황 API는 표준 표(TABLE-GROUP ACLASS="INV_PRT")에 적힌 줄만 돌려준다.
회사에 따라 표준 표에는 상장법인만 적고 비상장법인은 같은 머리줄의 표를 그 아래에 따로 붙인다
(포스코홀딩스: 표준 표 4줄, 따로 붙인 표에 100% 자회사 포스코를 포함한 나머지 전부).

따로 붙인 표는 형식이 자유라서, 표준 표와 칸 수가 같은 줄(16칸)만 읽는다:
  법인명, 상장여부, 최초취득일자, 출자목적, 최초취득금액, 기초(수량, 지분율, 장부가액),
  증가감소(수량, 금액, 평가손익), 기말(수량, 지분율, 장부가액), 최근사업연도(총자산, 당기순손익)
칸 수가 다른 줄은 읽지 않고 센다.
"""
import html
import re
from dataclasses import dataclass, field
from datetime import date

_SECTION_TITLE = re.compile(r"<TITLE[^>]*>[^<]*타법인\s*출자\s*현황\s*\(상세\)[^<]*</TITLE>")
_TABLE = re.compile(r"<TABLE\b[^>]*>(.*?)</TABLE>", re.S)
_GROUP = re.compile(r'<TABLE-GROUP ACLASS="INV_PRT".*?</TABLE-GROUP>', re.S)
_ROW = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S)
_CELL = re.compile(r"<T[DHEU]\b[^>]*>(.*?)</T[DHEU]>", re.S)
_AS_OF = re.compile(r"기준일\s*:?\s*(\d{4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})")
_TOTALS = re.compile(r"^(합\s*계|소\s*계|계|총\s*계)$")
COLUMNS = 16
END_PCT = 12


@dataclass
class Holding:
    name: str
    listed: str            # 상장여부 칸에 적힌 그대로
    purpose: str
    pct: str               # 기말 지분율, 적힌 그대로
    shares: str
    book_value: str


@dataclass
class Detail:
    as_of: date | None = None
    holdings: list[Holding] = field(default_factory=list)
    standard_rows: int = 0      # 표준 표에 있는 줄 수 (API가 돌려주는 것과 같은 범위)
    extra_tables: int = 0       # 따로 붙인 출자 표 수
    odd_rows: int = 0           # 칸 수가 달라 읽지 않은 줄


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _rows(table: str) -> list[list[str]]:
    return [[_text(c) for c in _CELL.findall(row)] for row in _ROW.findall(table)]


def parse(raw: bytes) -> Detail | None:
    """상세 장을 찾지 못하면 None."""
    text = raw.decode("utf-8", "replace")
    titles = list(_SECTION_TITLE.finditer(text))
    if not titles:
        return None
    start = titles[-1].end()
    end = text.find("</SECTION-2>", start)
    section = text[start:end if end > 0 else len(text)]
    detail = Detail()
    as_of = _AS_OF.search(_text(section[:20000]))
    if as_of:
        try:
            detail.as_of = date(*map(int, as_of.groups()))
        except ValueError:
            pass
    group = _GROUP.search(section)
    if group:
        detail.standard_rows = sum(1 for t in _TABLE.findall(group.group(0)) for r in _rows(t)
                                   if len(r) == COLUMNS and not _TOTALS.match(r[0]))
        section = section.replace(group.group(0), "")
    for table in _TABLE.findall(section):
        rows = _rows(table)
        head = " ".join(" ".join(r) for r in rows[:3])
        if "법인명" not in head or "지분율" not in head:
            continue
        detail.extra_tables += 1
        for row in rows:
            if not row or row[0] in ("법인명", "수량", "") or _TOTALS.match(row[0]) or "수량" in row[:2]:
                continue
            if len(row) != COLUMNS:
                detail.odd_rows += 1
                continue
            detail.holdings.append(Holding(name=row[0], listed=row[1], purpose=row[3], pct=row[END_PCT],
                                           shares=row[END_PCT - 1], book_value=row[END_PCT + 1]))
    return detail
