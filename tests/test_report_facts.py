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


def _stake_row(actor_id, decided, filed, value=710400000000):
    return {"subject": {"company_id": actor_id, "name": "크래프톤"}, "object": {"company_id": 9, "name": "K.K. BCJ-31"},
            "value": value, "as_of_date": decided, "disclosed_date": filed, "rcept_no": ["20260630800855"],
            "detail": {"shares": "132000000", "pct_after": "100"}}


def test_deals_text_separates_corrections_of_old_decisions():
    # 크래프톤(10판 H3): 2025-06 결의를 2026-06 에 정정한 공시를 '최근 1년의 결정 1건'으로 셌다
    from company_graph.report_facts import deals, deals_text
    d = deals(1, {"relations": []}, {"relations": [_stake_row(1, "2025-06-24", "2026-06-30")]}, {"relations": []}, 5, "2025-10-16")
    assert d["summary"]["stake_decisions"] == 0 and d["summary"]["corrections_of_older_stake_decisions"] == 1
    text = deals_text(d)
    assert "옛 지분 결정의 정정 공시 1건" in text
    assert "2025-06-24 결의, 2026-06-30 공시" in text
    assert "크래프톤이 K.K. BCJ-31 지분 132,000,000주를 7,104억 원에 취득하기로 결정" in text


def test_deals_text_counts_all_contracts_and_keeps_dates_apart():
    from company_graph.report_facts import deals, deals_text
    rows = [{"subject": {"company_id": 1, "name": "대우건설"}, "object": {"company_id": None, "name": f"조합{i}"},
             "value": (i + 1) * 100000000000, "as_of_date": "2026-09-16", "disclosed_date": "2026-09-17", "rcept_no": [f"2026091780005{i}"],
             "detail": {"title": f"사업{i}", "ratio_pct": "7.90"}} for i in range(10)]
    old = {**rows[0], "as_of_date": "2020-07-31", "disclosed_date": "2026-08-31",
           "detail": {"title": "산성구역", "changes": [["5. 계약금액", "- 542,470,330,151", "- 559,686,922,234"]]}}
    ended = {"relations": [{"subject": {"company_id": 1}, "object": {"name": "서부광역메트로"}, "value": 289600000000,
                            "as_of_date": "2026-04-09", "disclosed_date": "2026-04-10", "rcept_no": ["20260410800750"], "detail": {"reason": "주식양도"}}]}
    d = deals(1, {"relations": rows + [old]}, {"relations": []}, {"relations": []}, 0, "2025-10-16", ended, "2026-10-08")
    text = deals_text(d)
    assert "**공급계약 10건**" in text and "이 회사가 수주한 계약 10건, 합계 5조 5,000억 원" in text
    assert "2026-09-16 계약, 2026-09-17 공시" in text
    assert "나머지 2건" in text
    assert "옛 계약의 정정 공시 1건" in text and "정정 전 5,425억 원 → 정정 후 5,597억 원" in text
    assert "공급계약 해지 1건" in text and "2026-04-09 해지, 2026-04-10 공시 · 상대 서부광역메트로" in text
    assert "2026-10-08 접수분까지" in text
