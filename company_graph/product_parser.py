"""사업보고서 "주요 제품 및 서비스"의 표에서 제품(품목)과 매출 비중을 읽는다.

표의 칸 배치는 회사마다 다르다. 머리 줄에서 비중 칸을 찾고, 줄마다 그 자리의 숫자를 비중으로, 숫자가 아닌 칸을 이름으로 읽는다.
읽은 비중의 합이 100에 가까운 표만 믿는다. 합이 맞지 않으면 읽지 못한 것으로 친다.
"""
import re
from dataclasses import dataclass

_SHARE_HEAD = re.compile(r"비중|비율|구성비|점유율|%")
_NAME_HEAD = re.compile(r"품\s*목|제\s*품|상\s*품|서비스|사업\s*부문|부\s*문|매출\s*유형|유\s*형|구\s*분|용\s*도|내\s*용|상\s*표")
_TOTAL = re.compile(r"^(총|단순|매출|전체|연결)?(합계|총계|소계|계|total|합|총매출액?|매출총?액|매출액?합계|순매출액?)$", re.I)
_PAREN_NOTE = re.compile(r"\([^)]*\)|\[[^\]]*\]|[*※]\d*|주\d+\)")
_NUMBER = re.compile(r"^[△▲\-(]?\s*[\d,]+(\.\d+)?\s*%?\)?$")
_INLINE = re.compile(r"[\d,]+(?:\.\d+)?\s*\(\s*(△|-)?\s*(\d{1,3}(?:\.\d+)?)\s*%?\s*\)")   # "576,425(92.00%)"
_PERCENT = re.compile(r"^(△|▲|-)?\s*(\d{1,3}(?:\.\d+)?)\s*%?$")
_PAREN_PERCENT = re.compile(r"^\((\d{1,3}(?:\.\d+)?)%?\)$")   # "(16.9)": 괄호로 적은 음수
# 이름 칸이 아닌 것: 출시일("2014.12.23", "1999년"), 만든 회사("(주)덕성", "DUKSUNG VINA CO.,LTD.")
_NOT_NAME = re.compile(r"^(19|20)?\d{2}\s*[.년/-]|\(주\)|㈜|주식회사|유한공사|co\.\s*,?\s*ltd|LTD|Inc\.", re.I)


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
    squeezed = cell.replace(" ", "")
    plain = _PERCENT.match(squeezed)
    if plain and "," not in cell:
        value = float(plain.group(2))
        return -value if plain.group(1) else value
    negative = _PAREN_PERCENT.match(squeezed)
    if negative:
        return -float(negative.group(1))
    return None


def _is_total(name: str) -> bool:
    """합계 줄의 이름인가. "합 계(주1)", "단순합계", "매출 총계"도 합계다."""
    squeezed = _PAREN_NOTE.sub("", name).replace(" ", "")
    return _TOTAL.match(squeezed) is not None or re.search(r"(소계|합계|총계)$", squeezed) is not None


def _in_range(rows) -> bool:
    return len(rows) >= 1 and 97 <= sum(r.share for r in rows) <= 103 and all(-100 <= r.share <= 110 for r in rows)


def _without_totals(rows):
    """이름으로는 알아보지 못한 합계와 소계 줄을 값으로 가려 뺀다.

    합이 100이 아닐 때만 쓴다. (1) 그 줄만 100이고 나머지를 더해도 100이면 그 줄은 합계다.
    (2) 바로 앞의 줄들을 더한 값과 같은 줄은 소계다.
    """
    whole = [r for r in rows if 99.5 <= r.share <= 100.5]
    if len(whole) == 1 and len(rows) >= 3:
        rest = [r for r in rows if r is not whole[0]]
        if _in_range(rest):
            return rest
        rows = rest
    kept, run = [], []
    for row in rows:
        if len(run) >= 2 and abs(sum(r.share for r in run) - row.share) <= 0.15:
            run = []          # 소계: 앞의 줄들을 묶은 값이라 뺀다
            continue
        kept.append(row)
        run.append(row)
    return kept if len(kept) < len(rows) and _in_range(kept) else None


_GENERIC = re.compile(r"^(제품|상품|용역|기타|서비스|제품\s*및\s*상품|제품,\s*상품\s*등?|제품매출|상품매출|용역매출|기타매출|매출|내수|수출|국내|해외)(\s*등)?$")


_AMOUNT = re.compile(r"^\(?-?[\d,]+\)?$")
_SALES_HEAD = re.compile(r"매\s*출|수\s*익|금\s*액")
_PERIOD = re.compile(r"제\s*(\d+)\s*기|(20\d\d)")


def _amount(cell: str) -> float | None:
    """금액 칸의 값. "1,622,749", "(319,730)"(음수). 비율이나 연도처럼 보이는 것은 금액이 아니다."""
    squeezed = cell.replace(" ", "")
    if not _AMOUNT.match(squeezed) or ("," not in squeezed and len(squeezed.strip("()-")) < 4):
        return None
    value = float(squeezed.strip("()-").replace(",", ""))
    return -value if squeezed.startswith(("(", "-")) else value


_SUBTOTAL = re.compile(r"소계|subtotal", re.I)


def _nth_value(row: list[str], nth: int, value) -> int | None:
    """그 줄에서 nth 번째로 값이 읽히는 칸의 자리."""
    seen = 0
    for at, cell in enumerate(row):
        if value(cell) is not None:
            seen += 1
            if seen == nth:
                return at
    return None


def _column(table, body, width: int, from_end: int, value, nth: int = 0):
    """뒤에서 from_end 번째 칸을 값으로 보고 줄을 차례대로 읽는다. nth 를 주면 자리 대신 그 줄에서 nth 번째로 값이 읽히는 칸을 쓴다
    (줄마다 뒤에 붙는 칸 수가 달라 자리가 어긋나는 표: "… | 90.6 | 주요 거래처", "… | 0.1").

    돌려주는 것은 (종류, 값)의 목록이다. row 는 제품 줄(Product), total 은 합계 줄(수), subtotal 은 소계 줄(수),
    mixed 는 합계라는 말과 다른 이름이 함께 있는 줄(Product. "기타 | 기타 | 소계 | 3.3"처럼 줄이 하나뿐인 묶음의 소계).
    """
    seq, segment = [], None
    for row in body:
        if nth:
            at = _nth_value(row, nth, value)
            if at is None:
                continue
        elif len(row) < from_end:
            continue
        else:
            at = len(row) - from_end
        number = value(row[at])
        names = [cell for cell in row[:at] if cell and not _NUMBER.match(cell.replace(" ", "")) and not _NOT_NAME.search(cell)
                 and not _INLINE.search(cell) and re.search(r"[A-Za-z가-힣]", cell)]
        if number is None or not names:
            continue
        marks = [name for name in names if _is_total(name)]
        if marks and len(marks) == len(names):
            seq.append(("subtotal" if _SUBTOTAL.search(marks[0].replace(" ", "")) else "total", number))
            continue
        if marks:
            rest = [name for name in names if not _is_total(name)]
            seq.append(("mixed", Product(name=rest[-1][:100], segment=rest[0] if len(rest) >= 2 else None, share=number)))
            continue
        if len(row) == width and len(names) >= 2:
            segment = names[0]
        specific = [n for n in (names[1:] if len(row) == width and len(names) >= 2 else names) if not _GENERIC.match(n)]
        name = specific[0] if specific else names[-1] if len(names) == 1 else names[1] if len(names) >= 2 else names[0]
        seq.append(("row", Product(name=name[:100], segment=segment if segment != name else None, share=number)))
    return seq


def _pick_shares(seq) -> list[Product] | None:
    """한 칸에서 읽은 줄들 가운데 더해서 100이 되는 묶음을 고른다."""
    rows = [item for kind, item in seq if kind == "row"]
    if _in_range(rows):
        return rows
    # 줄이 하나뿐인 묶음의 소계는 그 자체가 제품 줄이다
    with_mixed = [item for kind, item in seq if kind in ("row", "mixed")]
    if len(with_mixed) > len(rows) and _in_range(with_mixed):
        return with_mixed
    # 여러 회사의 표를 이어 붙인 표: 합계 줄(100)이 나올 때마다 한 묶음이다. 맨 앞 묶음이 이 회사의 것이다
    block = []
    for kind, item in seq:
        if kind == "total" and 99 <= item <= 101:
            if _in_range(block) and len(block) < len(with_mixed):
                return block
            break
        if kind in ("row", "mixed"):
            block.append(item)
    if len(rows) >= 3 and all(-100 <= r.share <= 110 for r in rows):
        return _without_totals(rows)
    return None


def _period(table, body, width: int, from_end: int) -> int:
    """그 칸이 어느 기간의 값인지를 머리 줄에서 읽어 클수록 나중이 되는 수로. 알 수 없으면 0.

    머리 줄의 칸 수가 값 줄과 같아 자리를 그대로 맞출 수 있을 때만 읽는다. 기간이 칸을 묶어 적힌 머리 줄은 짝을 확신할 수 없어 읽지 않는다.
    """
    first = next((i for i, row in enumerate(table) if row in body), len(table))
    for row in table[:first][::-1] + table[:first]:
        marks = [(i, m) for i, cell in enumerate(row) if (m := _PERIOD.search(cell))]
        if len(marks) < 2:
            continue
        score = lambda m: int(m.group(1)) + 3000 if m.group(1) else int(m.group(2))
        if len(row) == width:
            hit = [m for i, m in marks if i == width - from_end]
            if hit:
                return score(hit[0])
    return 0


def _read(table: list[list[str]]) -> list[Product] | None:
    """비중 칸을 머리 줄 이름으로 찾지 않고, 줄마다 같은 자리(뒤에서부터 센 자리)의 값을 더해 100이 되는 칸을 찾는다.

    머리 줄이 두 줄이거나 여러 기간이 나란히 있어도 읽힌다. 여러 칸이 100이 되면 머리 줄에서 가장 나중 기간인 칸을,
    기간을 알 수 없으면 가장 앞의 칸을 쓴다.
    """
    head = [cell for row in table[:3] for cell in row]
    headed = any(_SHARE_HEAD.search(cell) for cell in head)
    width = max(len(row) for row in table)
    body = [row for row in table if len(row) >= 2 and any(_share(cell) is not None for cell in row)]
    found = []
    for from_end in range(width, 0, -1):   # 앞 칸이 세로로 합쳐진 줄은 칸 수가 줄어들어서 뒤에서부터 센다
        rows = _pick_shares(_column(table, body, width, from_end, _share))
        if not rows:
            continue
        if not headed:
            # 머리 줄에 비중이라는 말이 없는 표: 값에 %나 소수점이 있고 합이 거의 정확히 100일 때만 믿는다
            cells = [row[len(row) - from_end] for row in body if len(row) >= from_end]
            marked = sum("%" in cell or "." in cell for cell in cells)
            if len(rows) < 2 or marked * 2 < len(cells) or not 99.5 <= sum(r.share for r in rows) <= 100.5:
                continue
        found.append((_period(table, body, width, from_end), rows))
    if not found and headed:
        # 자리로는 못 읽은 표: 줄마다 첫 번째(당기) 비중 칸을 읽는다. 값에 %나 소수점이 있는 표만
        cells = [row[at] for row in body if (at := _nth_value(row, 1, _share)) is not None]
        if cells and sum("%" in cell or "." in cell for cell in cells) * 2 >= len(cells):
            rows = _pick_shares(_column(table, body, width, 0, _share, nth=1))
            if rows and len(rows) >= 2:
                return rows
    if not found:
        return None
    return max(found, key=lambda pair: pair[0])[1] if len({period for period, _ in found}) > 1 else found[0][1]


def _read_amounts(table: list[list[str]]) -> list[Product] | None:
    """비중 칸이 없고 매출액만 있는 표: 합계 줄이 있고 줄들을 더한 값이 그 합계와 맞으면, 매출액을 합계로 나눠 비중을 구한다.

    합계 줄이 없는 표(매출이 큰 제품 몇 개만 적은 표)는 전체를 알 수 없어서 읽지 않는다.
    """
    head = [cell for row in table[:3] for cell in row]
    if not any(_SALES_HEAD.search(cell) for cell in head):
        return None
    width = max(len(row) for row in table)
    body = [row for row in table if len(row) >= 2 and any(_amount(cell) is not None for cell in row)]
    found = []
    for from_end in range(width, 0, -1):
        # 합계 줄(소계가 아닌 것)이 나오면 그때까지의 줄을 더한 값과 견준다. 맞는 첫 묶음을 쓴다
        rows, total = [], None
        for kind, item in _column(table, body, width, from_end, _amount):
            if kind in ("row", "mixed"):
                rows.append(item)
            elif kind == "total" and item > 0:
                if len(rows) >= 2 and abs(sum(r.share for r in rows) - item) <= item * 0.01:
                    total = item
                break
        if total is None:
            continue
        shares = [Product(name=r.name, segment=r.segment, share=round(r.share / total * 100, 2)) for r in rows]
        if _in_range(shares):
            found.append((_period(table, body, width, from_end), shares))
    if not found:
        return None
    return max(found, key=lambda pair: pair[0])[1] if len({period for period, _ in found}) > 1 else found[0][1]


_NOT_PRODUCT_TABLE = re.compile(r"매\s*출\s*처|거\s*래\s*처|고\s*객|판\s*매\s*경\s*로|판\s*매\s*방\s*법|경\s*로|지\s*역|국\s*가|수\s*주|납\s*품\s*처|계\s*약")
_DOMESTIC = re.compile(r"^(내수|수출|국내|해외|로컬|직수출)(매출)?$")
_ANONYMOUS = re.compile(r"^[A-Za-z가-힣]{1,2}\s*사$")   # "K사", "가사": 이름을 가린 거래처


def _is_product_table(table: list[list[str]]) -> bool:
    """"매출 및 수주상황" 절에는 매출처별, 판매경로별, 지역별, 수주 표가 함께 있다. 품목으로 나눈 표만 고른다."""
    head = " ".join(cell for row in table[:2] for cell in row)
    return _NAME_HEAD.search(head) is not None and _NOT_PRODUCT_TABLE.search(head) is None


def products(text: str, amounts: bool = False, strict: bool = False) -> list[Product] | None:
    """절의 글에서 매출 비중 표를 찾아 읽는다. 믿을 만한 표가 없으면 None. amounts 이면 매출액만 있는 표에서 비중을 구한다.

    strict 이면("주요 제품" 절이 아닌 곳) 품목으로 나눈 표 가운데 맨 앞의 것만 본다. 그 표를 못 읽으면 뒤의 표로 넘어가지 않는다.
    """
    for table in _tables(text):
        if strict and not _is_product_table(table):
            continue
        found = _read_amounts(table) if amounts else _read(table)
        if found and sum(bool(_ANONYMOUS.match(r.name.strip())) for r in found) * 2 >= len(found):
            found = None   # 줄의 절반 이상이 "K사" 같은 이름이면 거래처 표다
        if found and strict and any(_DOMESTIC.match(r.name.replace(" ", "")) for r in found):
            found = None   # 내수와 수출로 나눈 표는 무엇을 파는지 말해 주지 않는다
        if found:
            return found
        if strict:
            return None
    return None


def read_report(sections: list[tuple[str, str]], fallback: bool = False) -> tuple[list[Product], str] | None:
    """한 보고서의 절들(제목, 글)에서 제품 표를 읽는다. (제품 줄, 어떻게 읽었는지)

    "주요 제품" 절의 비중 표 → 같은 절의 매출액 표. fallback 이면 그 절은 보지 않고 "매출" 절(매출 및 수주상황)의
    비중 표 → 매출액 표를 본다. 이쪽은 품목이 아닌 것으로 나눈 표가 섞여 있어, 어느 보고서의 "주요 제품" 절도 읽지 못한 회사에만 쓴다.
    """
    main = [text for title, text in sections if "주요 제품" in title]
    sales = [text for title, text in sections if "매출" in title and "주요 제품" not in title]
    ways = ((sales, False, "sales_share"), (sales, True, "sales_amount")) if fallback else ((main, False, "share"), (main, True, "amount"))
    for texts, amounts, how in ways:
        for text in texts[:1]:   # 한 보고서에 같은 소제목이 둘이면 앞의 것
            found = products(text, amounts=amounts, strict=how.startswith("sales"))
            if found:
                return found, how
    return None
