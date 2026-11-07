from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from company_graph.db import Base, CapitalEvent, Company, Document, Relation
from company_graph.memo_followups import memo_followups, title_amounts


def test_title_amounts():
    assert title_amounts("HD한국조선해양, 암모니아선 6301억 수주") == [630_100_000_000]
    assert title_amounts("삼성바이오, 2.7조 폴리펩타이드 인수") == [2_700_000_000_000]
    assert title_amounts("1조 2,000억 규모 계약") == [1_200_000_000_000]
    assert title_amounts("0.59점 차이로…한화오션 우선협상대상자") == []


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([Company(company_id=1, name="회사1", stock_code="000001", corp_cls="Y", in_scope=True),
                Company(company_id=2, name="회사2", stock_code="000002", corp_cls="Y", in_scope=True)])
    docs = [("20261005000001", 1, "단일판매ㆍ공급계약체결", date(2026, 10, 5)),      # 조회 시점 그날: 분석이 이미 볼 수 있었다
            ("20261006000001", 1, "단일판매ㆍ공급계약체결", date(2026, 10, 6)),      # 뉴스 날: 금액이 뉴스와 같다
            ("20261008000001", 1, "[기재정정]단일판매ㆍ공급계약체결", date(2026, 10, 8)),
            ("20261006000002", 2, "단일판매ㆍ공급계약체결", date(2026, 10, 6)),      # 다른 회사
            ("20261220000001", 1, "단일판매ㆍ공급계약체결", date(2026, 12, 20))]     # 60일 밖
    for no, cid, name, day in docs:
        db.add(Document(rcept_no=no, company_id=cid, report_nm=name, rcept_dt=day, doc_type="supply_contract", fetched_at=datetime(2026, 11, 1)))
        db.add(Relation(subject_company_id=cid, object_name_raw="상대 회사", rel_type="supply_contract", value_num=Decimal(630_000_000_000 if no.endswith("6000001") else 100_000_000_000),
                        value_unit="krw", disclosed_date=day, rcept_no=no, extract_method="api", trust_tier=1, attrs={"title": "암모니아선 2척"}))
    db.add(CapitalEvent(company_id=1, rcept_no="20261007000009", kind="buyback", report_date=date(2026, 10, 7), amount=5_000_000_000))
    db.commit()
    return db


def test_filings_after_the_analysis():
    out = memo_followups(_db(), date(2026, 10, 5), [1], "회사1, 암모니아선 6301억 수주")
    assert [f["rcept_no"] for f in out["filings"]] == ["20261006000001", "20261007000009", "20261008000001"]
    first, buyback, fixed = out["filings"]
    assert first["kind"] == "공급계약" and first["what"] == "암모니아선 2척" and first["same_amount"] is True   # 6300억 ≈ 6301억
    assert buyback["kind"] == "자기주식 취득" and buyback["same_amount"] is False
    assert fixed["corrected"] is True and fixed["same_amount"] is False
    assert (out["count"], out["same_amount"], out["contracts"]) == (3, 1, 2)
    assert out["checked_through"] == "2026-12-20" and out["until"] == "2026-12-04"


def test_scoreboard_counts_by_news_kind():
    from company_graph.db import NewsMemo
    from company_graph.memo_followups import scoreboard

    db = _db()
    memo = {"subjects": [{"company_id": 1, "name": "회사1"}], "related": []}
    for kind, title in (("수주·공급계약", "회사1, 암모니아선 6301억 수주"),      # 10-06 공급계약 6300억과 같다
                        ("수주·공급계약", "회사1, 9천만 달러 수주"),             # 제목에 원 금액이 없다
                        ("실적", "회사1 3분기 영업익 6300억")):                  # 실적 공시는 받지 않아 세지 않는다
        db.add(NewsMemo(news_date=date(2026, 10, 6), company_id=1, title=title, source="test", picked={"kind": kind},
                        events=[], result={"as_of": "2026-10-05", "memo": memo}, cost_usd=0))
    db.commit()
    out = scoreboard(db)
    contract, earnings = out["kinds"]
    assert out["analyses"] == 3 and contract["kind"] == "수주·공급계약" and earnings["checkable"] is False
    assert (contract["analyses"], contract["with_amount"], contract["with_filing"], contract["same_amount"]) == (2, 1, 2, 1)
    assert contract["hits"][0]["days_after_news"] == 0
    assert (earnings["with_amount"], earnings["with_filing"], earnings["same_amount"]) == (1, 0, 0)
