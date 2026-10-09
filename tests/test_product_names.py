"""제품 줄에서 표준 이름을 붙일 거리를 고르는 규칙."""
from company_graph.product_names import candidate, plain
from company_graph.product_parser import products


def test_plain_words():
    for text in ("기타", "기 타", "제품/상품 등", "상품매출", "임대수익 등", "내부거래 및 연결조정", "제 품상 품", "기타(*1)", "수출"):
        assert plain(text), text
    for text in ("광고매출", "화장품", "자동차부품", "해상화물운송", "기타 진주광택안료 시리즈"):
        assert not plain(text), text


def test_candidate_falls_back_to_segment():
    assert candidate("반도체 사업부문", "제 품") == "반도체 사업부문"
    assert candidate("제품", "화학약품재생장치") == "화학약품재생장치"
    assert candidate("기타", "임대") is None
    assert candidate(None, "상품") is None


def test_table_with_launch_date_column():
    # 품목 옆에 출시일 칸이 있는 표. 날짜를 이름으로 읽지 않는다
    text = "\n".join(["품목 | 출시일 | 매출액 | 비율(%)", "암레스트 | 1999년 | 100 | 40.0", "시트패드 | 1969년 | 150 | 60.0"])
    assert [(p.name, p.share) for p in products(text)] == [("암레스트", 40.0), ("시트패드", 60.0)]


def test_spelling_and_clean():
    from company_graph.name_products import clean, spelling

    assert spelling("통신장비") == spelling("통신 장비")
    assert spelling("2차전지 분리막") == spelling("이차전지 분리막")
    # 모델이 뭉뚱그린 말이나 같은 이름을 두 번 돌려줘도 걸러진다
    assert clean(["전기밥솥", "기타", " 전기밥솥 ", "주방  가전", "밥솥", "가전"]) == ["전기밥솥", "주방 가전", "밥솥"]


def test_product_id_is_stable_and_negative():
    from company_graph.api import product_id

    assert product_id("전기밥솥") == product_id("전기밥솥") < -1000
    assert product_id("전기밥솥") != product_id("화장품")


def test_finance_is_split_by_product_name():
    from company_graph.product_naming import refile

    assert refile("금융 서비스", "자동차보험") == "보험"
    assert refile("금융 서비스", "은행 여수신") == "은행·저축은행"
    assert refile("금융 서비스", "증권 중개") == "증권·투자은행"
    assert refile("금융 서비스", "리스") == "여신·카드·캐피탈"
    assert refile("금융 서비스", "벤처 투자") == "자산운용·투자"
    assert refile("금융 서비스", "신용 평가") == "신용정보·리서치"   # 따로 옮긴 것이 먼저다
    assert refile("금융 서비스", "세금 환급 대행") == "금융 서비스"
    assert refile("게임", "모바일 게임") == "게임"
