"""제품 줄에서 표준 이름을 붙일 거리를 고른다. 규칙만 쓴다(모델을 부르지 않는다).

회사마다 표의 칸을 다르게 쓴다. 품목 칸에 "제품", "기타"만 적고 정작 무엇인지는 사업부문 칸에 적은 회사가 많다.
품목 이름이 뭉뚱그린 말이면 사업부문을 대신 쓰고, 둘 다 그러면 붙일 것이 없다고 본다.
"""
import re

from .names import _FOOTNOTE

_PLAIN = ("제품", "상품", "용역", "기타", "서비스", "제", "상", "반제품", "원재료", "부산물", "부산품", "내수", "수출", "국내", "해외",
          "합계", "단순", "임대료", "임대", "부동산", "연결조정", "내부거래", "제거", "조정", "자사", "당사", "해당없음", "일반", "공통",
          "지주회사", "지주", "배당금", "배당", "지분법", "이익", "상표권", "브랜드", "로열티", "수수료", "이자", "투자",
          "others", "other", "etc", "한국", "미국", "중국", "일본", "유럽", "아시아", "인도", "베트남", "북미", "지역",
          # 낱말 끝에 붙는 말
          "매출액", "매출", "수익", "수입", "판매", "사업부문", "사업부", "사업", "부문", "등", "외", "류", "및", "外")
_ONLY_PLAIN = re.compile(rf"(?:{'|'.join(sorted(map(re.escape, _PLAIN), key=len, reverse=True))}|[\s/,·ㆍ&+()\[\]:;.\-])*", re.I)


def plain(text: str | None) -> bool:
    """"기타", "제 품상 품", "임대수익 등", "내부거래 및 연결조정"처럼 무엇을 파는지 알 수 없는 말로만 된 글인가."""
    return _ONLY_PLAIN.fullmatch(re.sub(r"\s", "", _FOOTNOTE.sub("", text or ""))) is not None


def candidate(segment: str | None, name: str) -> str | None:
    """표준 이름을 붙일 글. 품목 이름이 뭉뚱그린 말이면 사업부문을 쓰고, 둘 다 그러면 None."""
    if not plain(name):
        return name
    return segment if segment and not plain(segment) else None
