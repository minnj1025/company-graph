"""답에 적힌 숫자가 조회 결과에 실제로 있는지 코드로 확인한다.

Agent의 답에는 값마다 근거 접수번호가 붙는다. 그 점을 이용해, 검토를 두 번째 모델에 맡기지 않고
답의 지분율·주식수·금액·날짜·접수번호를 그 대화에서 받은 도구 결과와 대조한다.
어긋난 값이 있으면 agent.answer 가 그 목록을 돌려주고 한 번만 다시 쓰게 한다 (환경 변수 ASK_VERIFY=1).

직접 계산한 값(차이, 합계)은 조회 결과에 없으므로, 같은 줄에 계산했다는 말이 있으면 넘어간다.
"""
import re
from datetime import date

# 같은 줄에 이 말이 있으면 그 줄의 숫자는 답이 계산한 값으로 보고 대조하지 않는다
COMPUTED = re.compile(r"계산|차이|차액|더한|더하면|합치면|합산|합계|총액|총 |뺀|빼면|늘었|늘어|줄었|줄어|증가|감소|증감|변동|올랐|내렸|%p|약 |대략|평균|배\b")
NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
UNITS = {"조": 10**12, "억": 10**8, "만": 10**4}
# '89조 4,924억', '1조', '5,971억', '3,000만': 도구가 금액을 이렇게 글로 준다(financials.readable). 한 값으로 읽는다
WORDED = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)조(?!(?!원)[가-힣])(?:\s?(\d[\d,]*)\s?억)?|(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)억|(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)만(?=\s?원)")
# 숫자와 단위가 붙어 있을 때만 금액으로 읽는다 ("2026-03-31 조회", "B787 조립"의 조는 단위가 아니다)


def _worded(text: str) -> list[tuple[float, float, str]]:
    """글로 적은 금액들: (원 단위 값, 그 표기의 반올림 폭, 원문)."""
    out = []
    for m in WORDED.finditer(text):
        jo, eok, alone, man = m.groups()
        number = lambda s: float(s.replace(",", ""))
        if jo is not None:
            out.append((number(jo) * 1e12 + (number(eok) * 1e8 if eok else 0), 1e8 if eok else 1e12 * (0.1 if "." in jo else 1), m.group(0)))
        elif alone is not None:
            out.append((number(alone) * 1e8, 1e8 * (0.1 if "." in alone else 1), m.group(0)))
        else:
            out.append((number(man) * 1e4, 1e4, m.group(0)))
    return out


def _numbers(text: str) -> set[float]:
    out = {value for value, _, _ in _worded(text)}
    for token in NUMBER.findall(text):
        cleaned = token.replace(",", "").rstrip(".")
        try:
            out.add(float(cleaned))
        except ValueError:
            pass
    return out


def _dates(text: str) -> set[str]:
    found = {m.group(0) for m in re.finditer(r"\d{4}-\d{2}-\d{2}", text)}
    for y, m, d in re.findall(r"(\d{4})[.년]\s?(\d{1,2})[.월]\s?(\d{1,2})", text):
        found.add(f"{y}-{int(m):02d}-{int(d):02d}")
    for y, m, d in re.findall(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", text):
        found.add(f"{y}-{m}-{d}")
    return found


def _in_units(value: float, digits: int, unit: int, known: set[float]) -> bool:
    """'1,234억'처럼 단위로 줄여 쓴 금액: 조회 결과의 어떤 값을 그 단위로 반올림(또는 버림)하면 이 숫자가 되는가."""
    for raw in known:
        if raw < unit:
            continue
        scaled = raw / unit
        if round(scaled, digits) == value or int(scaled * 10**digits) / 10**digits == value:
            return True
    return False


def unsupported(answer: str, results: list[str], as_of: date | None = None, question: str = "") -> list[str]:
    """답에 있지만 조회 결과에서 확인되지 않는 값. 지분율(%), 쉼표가 든 큰 수, 조·억·만 단위 금액, 날짜, 접수번호를 본다."""
    body = "\n".join([*results, question])   # 질문에 나온 숫자(조건으로 준 기준값, 기간)는 답이 되받아 적어도 된다
    known, known_dates = _numbers(body), _dates(body)
    if as_of:
        known_dates.add(as_of.isoformat())
    flagged: list[str] = []
    skip: set[int] = set()   # 표에서 머리 줄이 계산값이라고 밝힌 칸 (차이, 증감, 합계 등)
    for line in answer.splitlines():
        table = line.strip().startswith("|")
        if not table:
            skip = set()
        elif not re.search(r"\d", line):   # 숫자가 없는 표 줄은 머리 줄이거나 구분선이다
            cells = line.strip().strip("|").split("|")
            if any(re.search(r"[가-힣A-Za-z]", c) for c in cells):
                skip = {i for i, c in enumerate(cells) if COMPUTED.search(c)}
            continue
        if table and skip:
            cells = line.strip().strip("|").split("|")
            line = " | ".join(c for i, c in enumerate(cells) if i not in skip)
        elif COMPUTED.search(line):
            continue
        for match in re.finditer(r"(\d[\d,]*\.?\d*)\s?%", line):                       # 지분율·비율
            value = float(match.group(1).replace(",", ""))
            if value not in known and round(value, 4) not in {round(k, 4) for k in known if k <= 100}:
                flagged.append(match.group(0).strip())
        for value, width, written in _worded(line):                                     # '89조 4,924억 원'은 한 값이다
            if not any(abs(value - k) <= width for k in known):
                flagged.append(written.strip())
        line = WORDED.sub(" ", line)
        for match in re.finditer(r"(\d[\d,]*\.?\d*)\s?(조|억|만)(?=\s?원|[\s,.)]|$)", line):      # 1,234억 원, 2.5조
            text = match.group(1).replace(",", "")
            digits = len(text.split(".")[1]) if "." in text else 0
            if not _in_units(float(text), digits, UNITS[match.group(2)], known):
                flagged.append(match.group(0).strip())
        for match in re.finditer(r"(?<![\d.$,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?!\s?(?:조|억|만|%))", line):  # 3,200,713주, 123,456,789,000원
            if float(match.group(1).replace(",", "")) not in known:
                flagged.append(match.group(1))
        for found in _dates(line):                                                      # 계약일, 기준일
            if found not in known_dates and found[:4] >= "2015":
                flagged.append(found)
    for number in re.findall(r"\b\d{14}\b", answer):                                     # 접수번호
        if number not in body:
            flagged.append(number)
    return list(dict.fromkeys(flagged))


def feedback(values: list[str]) -> str:
    return ("검증: 방금 답에 적힌 값 가운데 이번 대화의 조회 결과에서 확인되지 않는 것이 있습니다: " + ", ".join(values[:12]) + ". "
            "조회 결과를 다시 보고, 잘못 옮긴 값이면 고치고 다른 줄의 값을 붙였으면 바로잡으세요. 필요하면 도구를 다시 불러도 됩니다. "
            "직접 계산한 값이면 그 줄에 계산한 값이라고 밝히세요. 확인되지 않는 값은 답에서 빼세요. 답 전체를 처음부터 다시 쓰고, 이 검증에 대해서는 답에 언급하지 않습니다.")
