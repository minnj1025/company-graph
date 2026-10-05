"""사업보고서 원문의 "계열회사 현황(상세)" 표를 읽는다. 정해진 표라 규칙만 쓴다.

원문에서 이 표는 <TABLE-GROUP ACLASS="AFF_CMP"> 안에 있다.
  (기준일 : 2025년 12월 31일)
  상장여부 | 회사수 | 기업명 | 법인등록번호
  상장     | 12     | 현대자동차 | 110111-0085450      ← 상장여부·회사수는 첫 줄에만 있다
                    | 기아       | 110111-0037998
  비상장   | 62     | ...
회사수 칸이 있어서 읽은 줄 수가 맞는지 스스로 검사할 수 있다.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime

_GROUP = re.compile(r'<TABLE-GROUP[^>]*ACLASS="AFF_CMP"[^>]*>(.*?)</TABLE-GROUP>', re.S)
_ROW = re.compile(r"<TR[^>]*>(.*?)</TR>", re.S)
_CELL = re.compile(r"<T[DEHU][^>]*>(.*?)</T[DEHU]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_BASE_DATE = re.compile(r'AUNIT="BASE_DT"[^>]*AUNITVALUE="(\d{8})"')
_JURIR = re.compile(r"^\d{6}\s*-?\s*\d{7}$")


@dataclass
class Affiliate:
    name: str
    jurir_no: str | None
    listed: bool | None


@dataclass
class AffiliateTable:
    as_of: date | None = None
    affiliates: list[Affiliate] = field(default_factory=list)
    declared_count: int | None = None   # 표의 "회사수"를 더한 값

    @property
    def count_matches(self) -> bool | None:
        return None if self.declared_count is None else self.declared_count == len(self.affiliates)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub("", fragment).replace("&amp;", "&").replace("&nbsp;", " ")).strip()


def parse(raw: bytes) -> AffiliateTable | None:
    """표가 없으면 None (계열회사가 없거나 옛 양식)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("euc-kr", "replace")
    group = _GROUP.search(text)
    if not group:
        return None
    table = AffiliateTable()
    base = _BASE_DATE.search(group.group(1))
    if base:
        table.as_of = datetime.strptime(base.group(1), "%Y%m%d").date()
    listed, declared = None, 0
    for row in _ROW.findall(group.group(1)):
        cells = [c for c in (_text(c) for c in _CELL.findall(row)) if c]
        if not cells or "법인등록번호" in cells or "기준일" in cells[0]:
            continue
        if cells[0] in ("상장", "비상장"):
            listed = cells[0] == "상장"
            if len(cells) > 1 and cells[1].replace(",", "").isdigit():
                declared += int(cells[1].replace(",", ""))
                cells = cells[2:]
            else:
                cells = cells[1:]
        if listed is None or not cells:
            continue
        if _JURIR.match(cells[-1]):
            if len(cells) >= 2:
                table.affiliates.append(Affiliate(cells[-2], re.sub(r"\D", "", cells[-1]), listed))
        elif len(cells) <= 2 and cells[0] not in ("-", "해당사항 없음", "해당없음"):
            # 해외 계열회사는 법인등록번호 칸이 "-"이거나 비어 있다
            table.affiliates.append(Affiliate(cells[0], None, listed))
    table.declared_count = declared or None
    return table if table.affiliates else None
