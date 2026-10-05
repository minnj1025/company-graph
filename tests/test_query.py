"""조회 계층의 규칙: 시점, 지분 합치기, 계열 최신본, 경로."""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from company_graph import query
from company_graph.db import Base, Company, CompanyAlias, Document, Relation
from company_graph.names import normalize

HMC, KIA, MOBIS, SUPPLIER, SHIPPER = 1, 2, 3, 4, 5


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for cid, name in [(HMC, "현대자동차(주)"), (KIA, "기아(주)"), (MOBIS, "현대모비스(주)"),
                          (SUPPLIER, "부품사(주)"), (SHIPPER, "해운사(주)")]:
            s.add(Company(company_id=cid, name=name, corp_cls="Y", in_scope=True))
            s.add(CompanyAlias(alias=normalize(name), company_id=cid, source="auto"))
        s.flush()

        def add(subject, obj, rel_type, rcept_no, filer, *, value=None, unit=None, as_of=None, invalidated=None, source=None):
            if s.get(Document, rcept_no) is None:
                annual = rel_type in ("equity", "affiliate")
                s.add(Document(rcept_no=rcept_no, company_id=filer, report_nm="보고서",
                               doc_type="annual" if annual else "supply_contract", bsns_year=as_of.year if annual else None,
                               rcept_dt=datetime.strptime(rcept_no[:8], "%Y%m%d").date(), fetched_at=datetime.now()))
                s.flush()
            s.add(Relation(subject_company_id=subject, object_company_id=obj, object_name_raw="-", rel_type=rel_type,
                           value_num=value, value_unit=unit, as_of_date=as_of,
                           disclosed_date=datetime.strptime(rcept_no[:8], "%Y%m%d").date(), invalidated_date=invalidated,
                           rcept_no=rcept_no, extract_method="api", trust_tier=1, attrs={"source": source}))

        # 기아 → 현대모비스 지분: 2024년 말 17.66%(2025-03 공개), 2025년 말 18.10%(2026-03 공개, 두 표에 다 나옴)
        add(KIA, MOBIS, "equity", "20250313000001", KIA, value=Decimal("17.66"), unit="pct", as_of=date(2024, 12, 31),
            source="other_corp_investments")
        add(KIA, MOBIS, "equity", "20260312000001", KIA, value=Decimal("18.10"), unit="pct", as_of=date(2025, 12, 31),
            source="other_corp_investments")
        add(KIA, MOBIS, "equity", "20260309000001", MOBIS, value=Decimal("18.1"), unit="pct", as_of=date(2025, 12, 31),
            source="largest_shareholders")
        add(HMC, KIA, "equity", "20260318000001", HMC, value=Decimal("35.17"), unit="pct", as_of=date(2025, 12, 31),
            source="other_corp_investments")
        # 현대자동차가 2024년 말에는 부품사 지분을 갖고 있었지만 2025년 표에는 없다(처분)
        add(HMC, SUPPLIER, "equity", "20250320000002", HMC, value=Decimal("4.60"), unit="pct", as_of=date(2024, 12, 31),
            source="other_corp_investments")
        # 공급계약: 2026-07-24 공시가 2026-08-05에 정정됨
        add(SUPPLIER, HMC, "supply_contract", "20260724000001", SUPPLIER, value=Decimal(100), unit="krw",
            as_of=date(2026, 7, 24), invalidated=date(2026, 8, 5))
        add(SUPPLIER, HMC, "supply_contract", "20260805000001", SUPPLIER, value=Decimal(120), unit="krw",
            as_of=date(2026, 7, 24))
        add(SHIPPER, KIA, "supply_contract", "20260901000001", SHIPPER, value=Decimal(50), unit="krw", as_of=date(2026, 8, 31))
        # 계열: 현대자동차의 2024년 표에는 기아만, 2025년 표에는 기아와 현대모비스
        add(HMC, KIA, "affiliate", "20250320000002", HMC, as_of=date(2024, 12, 31))
        add(HMC, KIA, "affiliate", "20260318000001", HMC, as_of=date(2025, 12, 31))
        add(HMC, MOBIS, "affiliate", "20260318000001", HMC, as_of=date(2025, 12, 31))
        s.commit()
        yield s


def only(edges, rel_type):
    return [e for e in edges if e["type"] == rel_type]


def test_equity_shows_what_was_disclosed_by_then(db):
    before = only(query.relations(db, date(2026, 2, 1), company_ids=[MOBIS]), "equity")
    assert [(e["value"], e["as_of_date"]) for e in before] == [(Decimal("17.66"), date(2024, 12, 31))]
    after = only(query.relations(db, date(2026, 4, 1), company_ids=[MOBIS]), "equity")
    assert [(e["value"], e["as_of_date"]) for e in after] == [(Decimal("18.10"), date(2025, 12, 31))]


def test_equity_from_two_tables_becomes_one_edge_with_both_evidence(db):
    (edge,) = only(query.relations(db, date(2026, 4, 1), company_ids=[MOBIS]), "equity")
    assert edge["rcept_no"] == "20260312000001"  # 보유한 회사가 직접 낸 표를 쓴다
    assert edge["evidence"] == ["20260309000001", "20260312000001"]


def test_holder_side_is_enough_when_the_other_report_is_not_out_yet(db):
    (edge,) = only(query.relations(db, date(2026, 3, 10), company_ids=[MOBIS]), "equity")
    assert (edge["value"], edge["rcept_no"]) == (Decimal("18.1"), "20260309000001")


def test_sold_stake_disappears_once_the_newer_table_is_out(db):
    held = lambda day: sorted(e["object_id"] for e in only(
        query.relations(db, day, company_ids=[HMC], direction="out"), "equity"))
    assert held(date(2026, 1, 1)) == [SUPPLIER]
    assert held(date(2026, 4, 1)) == [KIA]


def test_corrected_contract_is_seen_as_of_each_date(db):
    amount = lambda day: [e["value"] for e in only(query.relations(db, day, company_ids=[SUPPLIER]), "supply_contract")]
    assert amount(date(2026, 7, 23)) == []
    assert amount(date(2026, 8, 1)) == [Decimal(100)]
    assert amount(date(2026, 8, 5)) == [Decimal(120)]


def test_affiliates_come_from_the_latest_visible_report(db):
    members = lambda day: sorted(e["object_id"] for e in query.group_members(db, HMC, day))
    assert members(date(2026, 1, 1)) == [KIA]
    assert members(date(2026, 4, 1)) == [KIA, MOBIS]
    # 직접 낸 보고서가 없는 회사는 자기를 계열로 적은 회사의 표를 쓴다
    assert sorted(e["object_id"] for e in query.group_members(db, MOBIS, date(2026, 4, 1))) == [KIA, MOBIS]


def test_path_between_two_companies(db):
    (route,) = query.paths(db, SUPPLIER, KIA, date(2026, 9, 30), rel_types=["equity", "supply_contract"])
    assert [(e["subject_id"], e["object_id"], e["type"]) for e in route] == [
        (SUPPLIER, HMC, "supply_contract"), (HMC, KIA, "equity")]
    assert query.paths(db, SUPPLIER, KIA, date(2026, 7, 1), rel_types=["equity", "supply_contract"]) == []


def test_neighborhood_merges_parallel_edges(db):
    graph = query.neighborhood(db, [HMC], date(2026, 9, 30))
    assert {n["id"] for n in graph["nodes"]} == {HMC, KIA, MOBIS, SUPPLIER}
    supply = [l for l in graph["links"] if l["type"] == "supply_contract"]
    assert [(l["source"], l["target"], l["count"]) for l in supply] == [(SUPPLIER, HMC, 1)]


def test_find_company_by_name_or_stock_code(db):
    assert [c.company_id for c in query.find_companies(db, "현대모비스")] == [MOBIS]
    assert [c.company_id for c in query.find_companies(db, "모비스")] == [MOBIS]
