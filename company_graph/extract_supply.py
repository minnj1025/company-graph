"""3단계: 공급계약 관계. 단일판매ㆍ공급계약 공시를 받아 양식을 읽고 relation 표에 넣는다.

주체 = 공시를 낸 회사(파는 쪽), 상대 = 계약상대(사는 쪽), 수치 = 계약금액(원), 기준일 = 계약(수주)일자.
정정 공시가 절반을 넘는다. 원래 줄은 지우지 않고 무효일(정정본 접수일)을 적는다.

실행: python -m company_graph.extract_supply
호출 수: 회사당 공시 목록 1건 + 공시 1건당 원문 1건. 지금 대상(약 176곳)은 수백 건.
전체 상장사로 넓히면 회사별이 아니라 기간별 목록으로 받는 편이 싸다 — 2024-01~2026-10의
공급계약 공시가 약 14,000건(2026-08~09 두 달 860건에서 어림)이라 하루 한도에 거의 찬다. 이틀에 나눠 받는다.
"""
import hashlib
import re
from collections import Counter, defaultdict
from datetime import date, datetime

from sqlalchemy import delete, select

from . import dart
from .cache import cached_json
from .db import Company, Document, QualityLog, Relation, init_db, session
from .extract_equity import alias_index, link, rcept_date
from .manual_aliases import GROUPS, SINGLE
from .names import clean_reported, normalize
from .supply_parser import SupplyContract, parse

SINCE = "20240101"
_AND_OTHERS = re.compile(r"\s*외\s*(\d+\s*(개사|개|사|인|곳))?\s*$")
_SPLIT = re.compile(r"\s*[,，、/]\s*|\s+및\s+")


class PartyResolver:
    """계약상대 칸에 적힌 글을 원장의 기업으로 바꾼다."""

    def __init__(self, db):
        self.index = alias_index(db)
        by_stock = {code: company_id for code, company_id in db.execute(
            select(Company.stock_code, Company.company_id).where(Company.stock_code.is_not(None),
                                                                 Company.corp_cls.in_(("Y", "K")))).all()}
        self.single = {normalize(k): by_stock[v] for k, v in SINGLE.items() if v in by_stock}
        self.groups = {normalize(k): [by_stock[c] for c in v if c in by_stock] for k, v in GROUPS.items()}

    def one(self, text: str) -> list[int]:
        key = normalize(clean_reported(_AND_OTHERS.sub("", text)))
        if key in self.groups:
            return self.groups[key]
        if key in self.single:
            return [self.single[key]]
        found = link(self.index, _AND_OTHERS.sub("", text))
        return [found] if found else []

    def resolve(self, text: str) -> list[int]:
        whole = self.one(text)
        if whole:
            return whole
        # "현대자동차, 현대모비스, 기아자동차"처럼 여러 회사를 한 칸에 적은 경우
        ids = [i for part in _SPLIT.split(text) if part for i in self.one(part)]
        return list(dict.fromkeys(ids))


def supply_filings(corp_code: str) -> list[dict]:
    today = date.today().strftime("%Y%m%d")
    rows = cached_json("dart_filings_I001", f"{corp_code}_{SINCE}_{today}", lambda: dart.filings(
        corp_code, bgn_de=SINCE, end_de=today, pblntf_detail_ty="I001"))
    return sorted((r for r in rows if "단일판매" in r["report_nm"]), key=lambda r: r["rcept_no"])


def ratio_matches(c: SupplyContract) -> bool | None:
    """계약금액 ÷ 최근매출액이 공시에 적힌 비율과 맞는가. 정답지 없이 파싱 오류를 잡는 검사."""
    if not (c.amount and c.recent_sales and c.ratio is not None):
        return None
    places = max(0, -c.ratio.as_tuple().exponent)
    computed = c.amount / c.recent_sales * 100
    return abs(computed - c.ratio) <= 10 ** -places  # 반올림·버림 어느 쪽이든 한 자리 안


def contract_key(c: SupplyContract) -> tuple:
    return (normalize(c.title or ""), c.contract_date, normalize(clean_reported(c.party or "")))


def load_company(db, company: Company, resolver: PartyResolver, stats: Counter):
    filings = supply_filings(company.corp_code)
    if not filings:
        return
    stats["공급계약 공시를 낸 회사"] += 1
    # 다시 돌려도 중복되지 않게 이 회사의 공급계약 줄과 문서를 지우고 새로 넣는다
    db.execute(delete(Relation).where(Relation.rel_type == "supply_contract",
                                      Relation.subject_company_id == company.company_id))
    db.execute(delete(Document).where(Document.doc_type == "supply_contract",
                                      Document.company_id == company.company_id))
    db.execute(delete(QualityLog).where(QualityLog.rcept_no.in_([f["rcept_no"] for f in filings])))
    db.flush()

    chains: dict[tuple, list[tuple[Document, list[Relation], SupplyContract]]] = defaultdict(list)
    loaded: list[tuple[Document, list[Relation], SupplyContract]] = []
    for filing in filings:
        rcept_no, report_nm = filing["rcept_no"], " ".join(filing["report_nm"].split())
        raw = dart.document(rcept_no)
        doc = Document(rcept_no=rcept_no, company_id=company.company_id, report_nm=report_nm,
                       rcept_dt=rcept_date(rcept_no), doc_type="supply_contract", is_correction="정정]" in report_nm,
                       is_latest=True,
                       content_sha256=hashlib.sha256(raw).hexdigest(), fetched_at=datetime.now())
        db.add(doc)
        stats["공시"] += 1
        if "해지" in report_nm:
            stats["해지 공시 (문서만 기록)"] += 1
            continue
        contract = parse(raw)
        if contract is None or (contract.party is None and contract.amount is None):
            stats["양식을 읽지 못함"] += 1
            db.add(QualityLog(check_name="range", rcept_no=rcept_no, created_at=datetime.now(),
                              detail=f"{company.name} {report_nm}: 공급계약 양식을 찾지 못함"))
            continue
        stats["정정 공시" if doc.is_correction else "최초 공시"] += 1
        check = ratio_matches(contract)
        if check is False:
            stats["금액÷매출액이 적힌 비율과 다름"] += 1
            db.add(QualityLog(check_name="cross_check", rcept_no=rcept_no, created_at=datetime.now(),
                              detail=f"{company.name}: {contract.amount} ÷ {contract.recent_sales} ≠ {contract.ratio}%"))
        elif check:
            stats["금액÷매출액이 적힌 비율과 맞음"] += 1

        raw_party = contract.party or "-"
        party_ids = [] if contract.party_hidden else resolver.resolve(raw_party)
        if contract.party_hidden:
            stats["계약상대 비공개"] += 1
        elif party_ids:
            stats["계약상대를 원장에 연결"] += 1
        else:
            stats["계약상대가 원장에 없음"] += 1
        attrs = {"kind": contract.kind, "title": contract.title, "party_relation": contract.party_relation,
                 "region": contract.region, "period_start": contract.period_start and contract.period_start.isoformat(),
                 "period_end": contract.period_end and contract.period_end.isoformat(),
                 "recent_sales": contract.recent_sales and str(contract.recent_sales),
                 "ratio_pct": contract.ratio and str(contract.ratio), "subsidiary": contract.subsidiary,
                 "party_hidden": contract.party_hidden, "joint_parties": len(party_ids) if len(party_ids) > 1 else None,
                 "correction_reason": contract.correction_reason,
                 "changes": [list(ch) for ch in contract.changes] or None}
        relations = [Relation(
            subject_company_id=company.company_id, object_company_id=party_id, object_name_raw=raw_party[:300],
            rel_type="supply_contract", value_num=contract.amount, value_unit="krw" if contract.amount else None,
            as_of_date=contract.contract_date, disclosed_date=doc.rcept_dt, rcept_no=rcept_no,
            extract_method="rule", trust_tier=1, attrs={k: v for k, v in attrs.items() if v is not None})
            for party_id in (party_ids or [None])]
        db.add_all(relations)
        stats["공급계약 관계"] += len(relations)
        loaded.append((doc, relations, contract))
        chains[contract_key(contract)].append((doc, relations, contract))

    # 정정 처리: 같은 계약(계약명·계약일·상대가 같음)의 공시를 접수 순으로 잇는다
    for doc, relations, contract in loaded:
        chain = chains[contract_key(contract)]
        position = chain.index((doc, relations, contract))
        previous = chain[position - 1] if position > 0 else None
        if previous is None and doc.is_correction:
            # 정정으로 계약상대가 바뀐 경우(예: ARAMCO → SATORP): 계약명과 계약일이 같고 아직 정정되지 않은 공시가 하나뿐이면 잇는다
            earlier = [x for x in loaded if x[0].rcept_no < doc.rcept_no and x[0].is_latest
                       and contract_key(x[2])[:2] == contract_key(contract)[:2]]
            previous = earlier[0] if len(earlier) == 1 else None
        if previous is None and doc.is_correction and contract.original_date:
            # 계약명 표기까지 바뀐 경우: 원래 공시 제출일과 상대가 같은 공시가 하나뿐이면 잇는다
            same_day = [x for x in loaded if x[0].rcept_dt == contract.original_date and x[0].rcept_no < doc.rcept_no
                        and x[0].is_latest and contract_key(x[2])[2] == contract_key(contract)[2]]
            previous = same_day[0] if len(same_day) == 1 else None
        if previous:
            doc.corrects_rcept_no = previous[0].rcept_no
            previous[0].is_latest = False
            for relation in previous[1]:
                relation.invalidated_date = doc.rcept_dt
            stats["정정으로 무효가 된 줄"] += len(previous[1])
        elif doc.is_correction:
            stats["정정인데 원래 공시가 수집 기간 밖"] += 1
            db.add(QualityLog(check_name="correction", rcept_no=doc.rcept_no, created_at=datetime.now(),
                              detail=f"{company.name}: 원래 공시({contract.original_date})를 찾지 못함"))


def main():
    engine = init_db()
    stats = Counter()
    with session(engine) as db:
        resolver = PartyResolver(db)
        companies = db.scalars(select(Company).where(Company.in_scope, Company.corp_code.is_not(None))).all()
        for company in companies:
            load_company(db, company, resolver, stats)
            db.commit()
    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")
    print(f"오늘 DART 호출 {dart.calls_today()}건")


if __name__ == "__main__":
    main()
