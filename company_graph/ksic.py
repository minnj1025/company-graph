"""한국표준산업분류(KSIC) 제11차 개정의 코드와 이름. 통계청 고시 원문에서 뽑은 ksic11.tsv 를 읽는다.

제품을 묶는 큰 범주(대분류, 중분류)는 이 공식 분류를 그대로 쓰고, 작은 범주는 제품군(product_families)을 쓴다.
세세분류까지 내려가면 공식 분류가 뭉개지는 곳이 많다("그 외 기타 …"에 제품 이름의 35%가 들어간다). 그래서 제품마다 세세분류 코드를
붙여 두기는 하되 회사를 묶는 단위로는 쓰지 않는다.
"""
from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).with_name("ksic11.tsv")


@lru_cache(maxsize=1)
def _table() -> tuple[dict[str, str], dict[str, str]]:
    names, section_of, section = {}, {}, None
    for line in _FILE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        code, name = line.split("\t")
        names[code] = name
        if len(code) == 1:
            section = code
        elif len(code) == 2:
            section_of[code] = section
    return names, section_of


def name(code: str) -> str:
    """코드의 공식 항목명. 없는 코드면 코드 그대로."""
    return _table()[0].get(code, code)


def section(code: str) -> str | None:
    """숫자 코드가 속한 대분류 글자. "26111" → "C" """
    return _table()[1].get(code[:2])


def short(code: str) -> str:
    """화면에 보일 짧은 이름. 공식 항목명에서 "제조업", "업" 같은 꼬리와 괄호 안 설명을 뗀다. 코드와 공식 이름은 따로 보여 준다."""
    text = name(code).split(";")[0].split("(")[0].strip()
    for tail in (" 개발 및 공급업", " 제조업", " 공급업", " 서비스업", "업"):
        if text.endswith(tail) and len(text) > len(tail) + 1:
            return text[: -len(tail)].strip()
    return text


def valid(code: str) -> bool:
    return code in _table()[0]
