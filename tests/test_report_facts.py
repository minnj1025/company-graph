"""보고서에 넘길 사실 다듬기."""
from company_graph.report_facts import _contract, corrections


def test_corrections_keep_direction_and_read_amounts_in_eok():
    # 계룡건설산업이 KT&G 와 맺은 계약의 정정(2026-06-01): 정정 전 1,148억 → 정정 후 1,136억
    rows = [["2. 계약내역 - 계약금액", "114,802,880,000", "113,623,285,000"], ["2. 계약내역 - 매출액대비(%)", "4.48", "4.44"],
            ["6. 주요 계약조건 - 계약금ㆍ선급금 유무", "-", "무"], ["9. 기타", "같은 문장", "같은 문장"]]
    assert corrections(rows) == ["2. 계약내역 - 계약금액: 정정 전 1,148억 원 → 정정 후 1,136억 원",
                                 "2. 계약내역 - 매출액대비(%): 정정 전 4.48 → 정정 후 4.44",
                                 "6. 주요 계약조건 - 계약금ㆍ선급금 유무: 정정 전 - → 정정 후 무"]


def test_contract_carries_corrections():
    r = {"subject": {"company_id": 1, "name": "계룡건설산업"}, "object": {"company_id": 2, "name": "KT&G"}, "value": 113623285000,
         "detail": {"changes": [["2. 계약내역 - 계약금액", "114,802,880,000", "113,623,285,000"]]}}
    item = _contract(2, r)
    assert item["corrections"] == ["2. 계약내역 - 계약금액: 정정 전 1,148억 원 → 정정 후 1,136억 원"]
    assert item["role"].startswith("이 회사가 산")
