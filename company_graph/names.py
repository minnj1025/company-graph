"""회사 이름 맞추기. 공시마다 표기가 달라서 비교 전에 같은 꼴로 만든다."""
import re
import unicodedata

_LEGAL_FORMS = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|유한책임회사|\(재\)|재단법인|\(사\)|사단법인")
_NOISE = re.compile(r"[\s\.,·ㆍ\-_'\"()\[\]]")


def normalize(name: str) -> str:
    """'현대모비스(주)', '현대모비스 주식회사', '㈜현대모비스' → '현대모비스'"""
    text = unicodedata.normalize("NFKC", name or "")
    text = _LEGAL_FORMS.sub("", text)
    return _NOISE.sub("", text).lower()


_FOOTNOTE = re.compile(r"_?\(\s*(?:주\s*\d+|[*※]\s*\d*)\s*\)|[*※]+\d*")  # '(주)'는 주석이 아니라 회사 형태다
_ASCII_PAREN = re.compile(r"\(\s*[A-Za-z0-9 .,&\-]+\s*\)")
_TRAILER = re.compile(r"(와|과)\s*그\s*종속(기업|회사).*$")


def clean_reported(raw: str) -> str:
    """공시 표에 적힌 이름에서 회사 이름이 아닌 부분을 뗀다.

    '명신산업(주)_(주2)' → '명신산업(주)', '㈜이노션에스 (INNS)' → '㈜이노션에스',
    '우리산업(주)와 그 종속기업' → '우리산업(주)'
    """
    text = unicodedata.normalize("NFKC", raw or "")
    text = _FOOTNOTE.sub("", text)
    text = _ASCII_PAREN.sub("", text)
    return _TRAILER.sub("", text).strip()
