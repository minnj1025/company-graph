from company_graph.display_names import brand, group_key, group_label, rename, tidy


def test_tidy_drops_legal_form_and_notes():
    assert tidy("㈜에이치비지주(*2)") == "에이치비지주"
    assert tidy("아트마이닝㈜(구,코나메타버스㈜)") == "아트마이닝"
    assert tidy("BNK캐피탈(주)(지분율 100%)") == "BNK캐피탈"
    assert tidy("( 주 )하림산업") == "하림산업"
    assert tidy("대신프라퍼티주식회사(구, 디에스한남주식회사)") == "대신프라퍼티"
    # 유동화전문유한회사는 회사 종류라 떼지 않는다
    assert tidy("에프아이1406유동화전문유한회사") == "에프아이1406유동화전문유한회사"


def test_brand_pairs_come_from_stock_names():
    assert brand("에이치엘비제약(주)", "HLB제약") == ("에이치엘비", "HLB")
    assert brand("에스케이하이닉스(주)", "SK하이닉스") == ("에스케이", "SK")
    assert brand("삼성전자(주)", "삼성전자") is None


def test_rename_only_with_evidence():
    assert rename("에이치엘비셀(주)", [("에이치엘비", "HLB")], confirmed=False) == "HLB셀"
    assert rename("씨제이이엔엠 스튜디오스(주)", [("씨제이", "CJ")], confirmed=True) == "CJ이엔엠 스튜디오스"
    # 근거가 없으면 바꾸지 않는다
    assert rename("에이치엘비셀(주)", [], confirmed=False) is None
    # 집단 소속이 확인되지 않았고 앞머리 뒤가 또 영문 글자 읽기면 건드리지 않는다
    assert rename("에스지이유니콘 주식회사", [("에스지", "SG")], confirmed=False) is None
    assert rename("엔씨소프트서비스", [("엔씨", "NC")], confirmed=False) is None


def test_group_labels_go_both_ways():
    assert group_label("에스케이") == "SK" and group_key("SK") == "에스케이"
    assert group_label("삼성") == "삼성" and group_key("삼성") == "삼성"
    assert group_label(None) is None
