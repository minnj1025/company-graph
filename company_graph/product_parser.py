"""사업보고서 "주요 제품 및 서비스"의 표에서 제품(품목)과 매출 비중을 읽는다.

표의 칸 배치는 회사마다 다르다. 머리 줄에서 비중 칸을 찾고, 줄마다 그 자리의 숫자를 비중으로, 숫자가 아닌 칸을 이름으로 읽는다.
읽은 비중의 합이 100에 가까운 표만 믿는다. 합이 맞지 않으면 읽지 못한 것으로 친다.
"""
import re
from dataclasses import dataclass

_SHARE_HEAD = re.compile(r"비중|비율|구성비|점유율|%")
_NAME_HEAD = re.compile(r"품\s*목|제\s*품|상\s*품|서비스|사업\s*부문|부\s*문|매출\s*유형|유\s*형|구\s*분|용\s*도|내\s*용|상\s*표")
_TOTAL = re.compile(r"^(총\s*)?(합\s*계|총\s*계|소\s*계|계|total|합)$", re.I)
_NUMBER = re.compile(r"^[△▲\-(]?\s*[\d,]+(\.\d+)?\s*%?\)?$")
_INLINE = re.compile(r"[\d,]+(?:\.\d+)?\s*\(\s*(△|-)?\s*(\d{1,3}(?:\.\d+)?)\s*%?\s*\)")   # "576,425(92.00%)"
_PERCENT = re.compile(r"^(△|▲|-)?\s*(\d{1,3}(?:\.\d+)?)\s*%?$")


@dataclass
class Product:
    name: str            # 표에 적힌 품목 이름 (사업부문과 품목이 따로 있으면 품목)
    segment: str | None  # 사업부문 칸이 따로 있으면 그 값
    share: float         # 매출 비중(%)


def _tables(text: str) -> list[list[list[str]]]:
    tables, current = [], []
    for line in text.split("\n"):
        if " | " in line:
            current.append([cell.strip() for cell in line.split(" | ")])
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _share(cell: str) -> float | None:
    inline = _INLINE.search(cell)
    if inline:
        return -float(inline.group(2)) if inline.group(1) else float(inline.group(2))
    plain = _PERCENT.match(cell.replace(" ", ""))
    if plain and "," not in cell:
        value = float(plain.group(2))
        return -value if plain.group(1) else value
    return None


_GENERIC = re.compile(r"^(제품|상품|용역|기타|서비스|제품\s*및\s*상품|제품,\s*상품\s*등?|제품매출|상품매출|용역매출|기타매출|매출|내수|수출|국내|해외)(\s*등)?$")


def _read(table: list[list[str]]) -> list[Product] | None:
    """비중 칸을 머리 줄 이름으로 찾지 않고, 줄마다 같은 자리(뒤에서부터 센 자리)의 값을 더해 100이 되는 칸을 찾는다.

    머리 줄이 두 줄이거나 여러 기간이 나란히 있어도 읽힌다. 여러 칸이 100이 되면 가장 앞의 것(당기)을 쓴다.
    """
    head = [cell for row in table[:3] for cell in row]
    if not any(_SHARE_HEAD.search(cell) for cell in head):
        return None
    width = max(len(row) for row in table)
    body = [row for row in table if len(row) >= 2 and any(_share(cell) is not None for cell in row)]
    for from_end in range(width, 0, -1):   # 앞 칸이 세로로 합쳐진 줄은 칸 수가 줄어들어서 뒤에서부터 센다
        rows, segment = [], None
        for row in body:
            if len(row) < from_end:
                continue
            at = len(row) - from_end
            share = _share(row[at])
            names = [cell for cell in row[:at] if cell and cell != "-" and not _NUMBER.match(cell.replace(" ", ""))]
            if share is None or not names:
                continue
            if any(_TOTAL.match(name.replace(" ", "")) for name in names):
                continue
            if len(row) == width and len(names) >= 2:
                segment = names[0]
            specific = [n for n in (names[1:] if len(row) == width and len(names) >= 2 else names) if not _GENERIC.match(n)]
            name = specific[0] if specific else names[-1] if len(names) == 1 else names[1] if len(names) >= 2 else names[0]
            rows.append(Product(name=name[:100], segment=segment if segment != name else None, share=share))
        if len(rows) >= 1 and 97 <= sum(r.share for r in rows) <= 103 and all(-100 <= r.share <= 110 for r in rows):
            return rows
    return None


def products(text: str) -> list[Product] | None:
    """절의 글에서 매출 비중 표를 찾아 읽는다. 믿을 만한 표가 없으면 None."""
    for table in _tables(text):
        found = _read(table)
        if found:
            return found
    return None
