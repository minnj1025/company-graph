"""사업보고서의 "II. 사업의 내용"을 소제목 단위로 잘라 글로 돌려준다.

2022년 서식 개정 뒤로 소제목은 일곱 개로 정해져 있다(사업의 개요, 주요 제품 및 서비스, 원재료 및 생산설비,
매출 및 수주상황, 위험관리 및 파생거래, 주요계약 및 연구개발활동, 기타 참고사항). 금융회사는 소제목이 다르다.
표의 칸 배치는 회사마다 달라서 값을 뽑지 않고, 줄 모양만 살린 글로 둔다.
"""
import html
import re
from dataclasses import dataclass

_TITLE = re.compile(r"<TITLE[^>]*>(.*?)</TITLE>", re.S)
_STYLE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.S | re.I)
_ROW = re.compile(r"<TR[^>]*>(.*?)</TR\s*>", re.S | re.I)
_CELL_END = re.compile(r"</(?:TD|TH|TE|TU)\s*>", re.I)
_BREAK = re.compile(r"<(?:BR|/P|/TABLE|/TITLE)[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_NUMBERED = re.compile(r"^(\d+)\s*[.\-]")
MAX_CHARS = 20000   # 한 절이 이보다 길면 자른다 (기타 참고사항에 업계 통계를 길게 붙이는 회사가 있다)


@dataclass
class Section:
    number: int          # 소제목 앞의 번호. 번호가 없으면 나온 순서
    title: str
    text: str
    truncated: bool = False


def _clean(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", markup)).replace("\xa0", " ")).strip()


def _row(match: re.Match) -> str:
    cells = [_clean(cell) for cell in _CELL_END.split(match.group(1))]
    return "\n" + " | ".join(cell for cell in cells if cell) + "\n"


def _plain(markup: str) -> str:
    """태그를 걷어 내되 표의 줄과 칸은 남긴다. 한 줄이 표의 한 줄이고 칸 사이는 " | " 이다."""
    text = _ROW.sub(_row, _STYLE.sub("", markup))
    text = _BREAK.sub("\n", text)
    return "\n".join(line for line in (_clean(part) for part in text.split("\n")) if line)


def sections(raw: bytes) -> list[Section]:
    """본문의 제목 태그로 자른다. 목차에도 같은 제목이 있지만 목차는 제목 태그가 아니라서 걸리지 않는다."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("euc-kr", "replace")
    titles = [(m.start(), m.end(), _clean(m.group(1))) for m in _TITLE.finditer(text)]
    start = next((i for i, t in enumerate(titles) if re.match(r"^II\.\s*사업의\s*내용", t[2])), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(titles)) if re.match(r"^III\.", titles[i][2])), len(titles))
    found = []
    for order, i in enumerate(range(start + 1, end), 1):
        stop = titles[i + 1][0] if i + 1 < len(titles) else len(text)
        body = _plain(text[titles[i][1]:stop])
        numbered = _NUMBERED.match(titles[i][2])
        if body:
            found.append(Section(number=int(numbered.group(1)) if numbered else order, title=titles[i][2][:100],
                                 text=body[:MAX_CHARS], truncated=len(body) > MAX_CHARS))
    if not found:   # 소제목 없이 본문만 쓴 보고서
        stop = titles[end][0] if end < len(titles) else len(text)
        body = _plain(text[titles[start][1]:stop])
        if body:
            found.append(Section(number=0, title=titles[start][2][:100], text=body[:MAX_CHARS], truncated=len(body) > MAX_CHARS))
    return found
