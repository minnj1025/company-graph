from company_graph.product_parser import products, read_report


def shares(rows):
    return [(row.name.replace(" ", ""), row.share) for row in rows]


def test_parenthesised_negative_adjustment_row():
    # 괄호로 적은 음수(매출에누리)를 읽어야 합이 100이 된다
    text = """(단위 : 백만원, %)
사업부문 | 매출유형 | 품 목 | 매출액 | 비율
식품 | 제품 | 라 면 | 1,622,749 | 85.9
제품 | 스 낵 | 257,318 | 13.6
제품 및 상품 | 음 료 | 61,797 | 3.3
제품, 상품 및 기타 | 기 타(상품+반제품) | 267,958 | 14.1
- | 매출에누리 등 | (319,730) | (16.9)
계 | 1,890,092 | 100.0"""
    rows = products(text)
    assert round(sum(row.share for row in rows), 1) == 100.0
    assert ("매출에누리등", -16.9) in shares(rows)


def test_total_row_with_other_wording():
    # "단순합계"도 합계 줄이다. 그 아래의 내부거래·순매출액 줄은 비중이 없어 읽지 않는다
    text = """구 분 | 영업유형 | 품 목 | 구체적 용도 | 매출액 | 비율
철강제조업 | 철강 | 봉 형 강 | 건축용 | 2,961,348 | 21.9%
판 재 | 9,706,570 | 71.7%
기타제품 | 산업용 | 141,205 | 1.0%
기타 | 반제품 外 | - | 722,137 | 5.3%
단순합계 | - | 13,531,260 | 100.0%
내부거래 | - | (1,684,270)
순매출액 | - | 11,846,990"""
    rows = products(text)
    assert [share for _, share in shares(rows)] == [21.9, 71.7, 1.0, 5.3]


def test_subtotals_skipped_but_single_row_group_kept():
    # 소계 줄은 빼되, 줄이 하나뿐인 묶음의 소계("기타 | 기타 | 소 계 | 3.3")는 그 자체가 제품 줄이다
    text = """사업부문 | 매출유형 | 품 목 | 매출액 | 비율(%)
음식료품제조 | 제품 | 비스킷 | 180,204 | 27.7
파 이 | 134,382 | 20.7
스 낵 | 195,513 | 30.1
기 타 | 50,436 | 7.8
소 계 | 560,535 | 86.3
상품 | 비스킷 | 3,076 | 0.5
스 낵 | 32,122 | 4.9
기 타 | 32,626 | 5.0
소 계 | 67,824 | 10.4
기타 | 기타 | 소 계 | 21,265 | 3.3
총 매 출 액 | 649,624 | 100.0"""
    rows = products(text)
    assert round(sum(row.share for row in rows), 1) == 100.0
    assert 86.3 not in [row.share for row in rows] and 3.3 in [row.share for row in rows]


def test_first_block_of_stacked_company_tables():
    # 여러 회사의 표를 이어 붙인 표는 맨 앞 묶음(이 회사)만 읽는다
    text = """구 분 | 주요 사업 내용 | 매출액 | 비율(%)
대한항공 | 여객노선(국제) | 52,373 | 57.2%
화물노선 | 26,325 | 27.6%
기타 | 14,415 | 15.2%
합 계 | 95,350 | 100.0%
진에어 | 여객노선(국제) | 6,174 | 78.8%
기타 | 1,659 | 21.2%
합 계 | 7,833 | 100.0%"""
    assert [share for _, share in shares(products(text))] == [57.2, 27.6, 15.2]


def test_amount_table_needs_a_matching_total():
    # 비중 칸이 없으면 매출액을 합계로 나눈다. 합계 줄이 없는 표(큰 제품 몇 개만 적은 표)는 읽지 않는다
    with_total = """품목 | 매출액
가전 | 7,000
부품 | 3,000
합계 | 10,000"""
    assert shares(products(with_total, amounts=True)) == [("가전", 70.0), ("부품", 30.0)]
    without_total = """품목군 | 적응증 | 제17기 반기 매출액
로수젯 | 고지혈증 | 90,732
아모잘탄 | 복합고혈압 | 50,851"""
    assert products(without_total, amounts=True) is None


def test_sales_section_only_as_fallback_and_only_product_tables():
    main = ("2. 주요 제품 및 서비스", "당사는 표를 싣지 않았습니다.")
    customers = ("4. 매출 및 수주상황", """매출처 | 매출액 | 비율
K사 | 800 | 80.0
L사 | 200 | 20.0""")
    by_item = ("4. 매출 및 수주상황", """품목 | 매출액 | 비율
콜라겐 | 780 | 78.0
유산균 | 220 | 22.0""")
    assert read_report([main, by_item]) is None                       # 먼저는 "주요 제품" 절만 본다
    assert shares(read_report([main, by_item], fallback=True)[0]) == [("콜라겐", 78.0), ("유산균", 22.0)]
    assert read_report([main, customers], fallback=True) is None      # 매출처로 나눈 표는 제품 표가 아니다
