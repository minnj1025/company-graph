"""3단계: 공급계약 관계. 단일판매ㆍ공급계약 공시를 받아 양식을 읽고 relation 표에 넣는다.

주체 = 공시를 낸 회사(파는 쪽), 상대 = 계약상대(사는 쪽), 수치 = 계약금액(원), 기준일 = 계약(수주)일자.
정정 공시가 절반을 넘는다. 원래 줄은 지우지 않고 무효일(정정본 접수일)을 적는다.

실행: python -m company_graph.extract_supply          수집 대상 기업이 낸 공시만 (회사별 목록, 수백 건)
      python -m company_graph.extract_supply --all    전 시장 (기간별 목록)
전 시장은 2024-01~2026-10의 공급계약 공시가 약 14,000건(2026-08~09 두 달 860건에서 어림)이라
목록 약 1,000건 + 원문 약 14,000건으로 DART 하루 한도(18,000건)에 거의 찬다.
받은 원문은 캐시에 남으므로 한도나 시간에 걸려 끊기면 같은 명령으로 이어 받는다.
"""
import hashlib
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from . import dart, loader
from .cache import cached_json
from .db import Company, Document, QualityLog, Relation, init_db, session
from .extract_equity import alias_index, link, own_affiliates, rcept_date
from .manual_aliases import GROUPS, SINGLE
from .names import clean_reported, normalize
from .supply_parser import SupplyContract, Termination, parse, parse_termination

SINCE = "20240101"
VERSION = "supply-3"  # 3: 해지 공시를 줄로 넣고 해지된 계약을 무효로. 2: 자기 계열회사 표를 먼저 보고 연결, 비율 검산을 반올림 구간으로, 줄을 지우지 않음
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

    def one(self, text: str, affiliates: dict[str, int]) -> list[int]:
        key = normalize(clean_reported(_AND_OTHERS.sub("", text)))
        if key in affiliates:  # 공시를 낸 회사 자신의 계열회사 표에 있는 이름이면 그 회사다
            return [affiliates[key]]
        if key in self.groups:
            return self.groups[key]
        if key in self.single:
            return [self.single[key]]
        found = link(self.index, _AND_OTHERS.sub("", text))
        return [found] if found else []

    def resolve(self, text: str, affiliates: dict[str, int]) -> list[int]:
        whole = self.one(text, affiliates)
        if whole:
            return whole
        # "현대자동차, 현대모비스, 기아자동차"처럼 여러 회사를 한 칸에 적은 경우
        ids = [i for part in _SPLIT.split(text) if part for i in self.one(part, affiliates)]
        return list(dict.fromkeys(ids))


def supply_filings(corp_code: str) -> list[dict]:
    today = date.today().strftime("%Y%m%d")
    rows = cached_json("dart_filings_I001", f"{corp_code}_{SINCE}_{today}", lambda: dart.filings(
        corp_code, bgn_de=SINCE, end_de=today, pblntf_detail_ty="I001"))
    return sorted((r for r in rows if "단일판매" in r["report_nm"]), key=lambda r: r["rcept_no"])


def market_supply_filings() -> dict[str, list[dict]]:
    """전 시장의 공급계약 공시를 회사별로. 회사를 지정하지 않으면 목록을 석 달씩만 받을 수 있다."""
    by_company: dict[str, list[dict]] = defaultdict(list)
    start, today = datetime.strptime(SINCE, "%Y%m%d").date(), date.today()
    while start <= today:
        end = min(start + timedelta(days=89), today)
        key = f"{start:%Y%m%d}_{end:%Y%m%d}"
        rows = cached_json("dart_filings_I001_market", key, lambda: dart.filings(
            bgn_de=f"{start:%Y%m%d}", end_de=f"{end:%Y%m%d}", pblntf_detail_ty="I001"))
        for row in rows:
            if "단일판매" in row["report_nm"]:
                by_company[row["corp_code"]].append(row)
        start = end + timedelta(days=1)
    return {code: sorted(rows, key=lambda r: r["rcept_no"]) for code, rows in by_company.items()}


def ratio_matches(c: SupplyContract) -> bool | None:
    """계약금액 ÷ 최근매출액이 공시에 적힌 비율과 맞는가. 정답지 없이 파싱 오류를 잡는 검사."""
    if not (c.amount and c.recent_sales and c.ratio is not None):
        return None
    # 적힌 비율은 한 점이 아니라 구간이다(XBRL Calculations 1.1의 생각). 2.57%라고 적혔으면
    # 반올림했을 때는 [2.565, 2.575), 버렸을 때는 [2.57, 2.58). 계산값이 둘 중 하나에 들면 일치로 본다
    unit = Decimal(1).scaleb(c.ratio.as_tuple().exponent)
    computed = c.amount / c.recent_sales * 100
    return c.ratio - unit / 2 <= computed < c.ratio + unit


def contract_key(c: SupplyContract) -> tuple:
    return (normalize(c.title or ""), c.contract_date, normalize(clean_reported(c.party or "")))


def load_company(db, company: Company, filings: list[dict], resolver: PartyResolver, stats: Counter):
    if not filings:
        return
    stats["공급계약 공시를 낸 회사"] += 1
    db.execute(delete(QualityLog).where(QualityLog.rcept_no.in_([f["rcept_no"] for f in filings])))
    affiliates = own_affiliates(db, company)
    failed_before = stats["원문을 받지 못함"]

    chains: dict[tuple, list[tuple[Document, list[Relation], SupplyContract]]] = defaultdict(list)
    loaded: list[tuple[Document, list[Relation], SupplyContract]] = []
    terminations: list[tuple[Document, Termination]] = []
    for filing in filings:
        rcept_no, report_nm = filing["rcept_no"], " ".join(filing["report_nm"].split())
        try:
            raw = dart.document(rcept_no)
        except dart.DailyBudgetExceeded:
            raise
        except dart.DartError:
            if "첨부정정" in filing["report_nm"]:
                # 첨부 서류만 바꾼 정정은 본문 파일이 없다(DART가 014를 돌려준다). 양식 내용은 그대로라 건너뛴다
                stats["첨부만 정정한 공시(본문 없음)"] += 1
            else:
                stats["원문을 받지 못함"] += 1
            continue
        doc = db.get(Document, rcept_no)
        if doc is None:
            doc = Document(rcept_no=rcept_no, company_id=company.company_id, report_nm=report_nm,
                           rcept_dt=rcept_date(rcept_no), doc_type="supply_contract", fetched_at=datetime.now())
            db.add(doc)
        # 정정 연결은 아래에서 매번 다시 계산한다
        doc.is_correction, doc.is_latest, doc.corrects_rcept_no = "정정]" in report_nm, True, None
        doc.content_sha256 = hashlib.sha256(raw).hexdigest()
        stats["공시"] += 1
        if "해지" in report_nm:
            stats["해지 공시"] += 1
            ended = parse_termination(raw)
            if ended is None or not (ended.title or ended.party):
                stats["해지 양식을 읽지 못함"] += 1
                db.add(QualityLog(check_name="range", rcept_no=rcept_no, created_at=datetime.now(),
                                  detail=f"{company.name} {report_nm}: 해지 양식을 찾지 못함"))
                continue
            terminations.append((doc, ended))
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
        party_ids = [] if contract.party_hidden else resolver.resolve(raw_party, affiliates)
        if contract.party_hidden:
            stats["계약상대 비공개"] += 1
        elif party_ids:
            stats["계약상대를 원장에 연결"] += 1
        else:
            stats["계약상대가 원장에 없음"] += 1
        attrs = {"kind": contract.kind, "title": contract.title, "party_relation": contract.party_relation,
                 "region": contract.region, "period_start": contract.period_start and contract.period_start.isoformat(),
                 "period_end": contract.period_end and contract.period_end.isoformat(),
                 "recent_sales": None if contract.recent_sales is None else str(contract.recent_sales),
                 "ratio_pct": None if contract.ratio is None else str(contract.ratio), "subsidiary": contract.subsidiary,
                 "party_hidden": contract.party_hidden, "joint_parties": len(party_ids) if len(party_ids) > 1 else None,
                 "correction_reason": contract.correction_reason,
                 "changes": [list(ch) for ch in contract.changes] or None}
        relations = [Relation(
            subject_company_id=company.company_id, object_company_id=party_id, object_name_raw=raw_party[:300],
            rel_type="supply_contract", value_num=contract.amount, value_unit="krw" if contract.amount else None,
            as_of_date=contract.contract_date, disclosed_date=doc.rcept_dt, rcept_no=rcept_no,
            extract_method="rule", trust_tier=1, attrs={k: v for k, v in attrs.items() if v is not None})
            for party_id in (party_ids or [None])]
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

    # 해지: 해지 사실을 따로 한 줄로 넣고, 같은 계약(계약명과 상대가 같음)의 지금 유효한 공시를 무효로 돌린다
    ended_rows: list[Relation] = []
    for doc, ended in terminations:
        raw_party = ended.party or "-"
        party_ids = resolver.resolve(raw_party, affiliates) if ended.party else []
        same = [x for x in loaded if x[0].rcept_no < doc.rcept_no and x[0].is_latest
                and normalize(x[2].title or "") == normalize(ended.title or "")
                and normalize(clean_reported(x[2].party or "")) == normalize(clean_reported(raw_party))]
        original = same[-1] if same else None
        if original is None and ended.related_dates:
            # 계약명이나 상대의 표기가 달라졌으면, 해지 공시의 "관련공시"에 적힌 날짜에 처음 공시된 계약 중에서 찾는다
            by_no = {x[0].rcept_no: x[0] for x in loaded}

            def first_filed(d: Document):
                while d.corrects_rcept_no in by_no:
                    d = by_no[d.corrects_rcept_no]
                return d.rcept_dt

            dated = [x for x in loaded if x[0].rcept_no < doc.rcept_no and x[0].is_latest
                     and (first_filed(x[0]) in ended.related_dates or x[0].rcept_dt in ended.related_dates)]
            if len(dated) > 1:
                dated = [x for x in dated if normalize(clean_reported(x[2].party or "")) == normalize(clean_reported(raw_party))]
            original = dated[0] if len(dated) == 1 else None
        if original:
            doc.corrects_rcept_no = original[0].rcept_no
            original[0].is_latest = False
            for relation in original[1]:
                relation.invalidated_date = doc.rcept_dt
            stats["해지로 무효가 된 줄"] += len(original[1])
        else:
            stats["해지인데 원래 계약이 수집 기간 밖이거나 찾지 못함"] += 1
        attrs = {"title": ended.title, "reason": ended.reason, "subsidiary": ended.subsidiary,
                 "terminated_filing": original[0].rcept_no if original else None}
        ended_rows += [Relation(
            subject_company_id=company.company_id, object_company_id=party_id, object_name_raw=raw_party[:300],
            rel_type="supply_termination", value_num=ended.amount, value_unit="krw" if ended.amount else None,
            as_of_date=ended.termination_date, disclosed_date=doc.rcept_dt, rcept_no=doc.rcept_no,
            extract_method="rule", trust_tier=1, attrs={k: v for k, v in attrs.items() if v is not None})
            for party_id in (party_ids or [None])]

    if stats["원문을 받지 못함"] > failed_before:
        # 못 받은 공시가 있으면 줄을 맞추지 않는다. 일시적인 실패 때문에 멀쩡한 줄을 내리면 안 된다
        stats["원문 실패로 줄 맞추기를 건너뛴 회사"] += 1
        return
    counts = loader.sync(db, [Relation.rel_type == "supply_contract", Relation.subject_company_id == company.company_id],
                         [relation for _, relations, _ in loaded for relation in relations], VERSION)
    ended_counts = loader.sync(db, [Relation.rel_type == "supply_termination",
                                    Relation.subject_company_id == company.company_id], ended_rows, VERSION)
    stats.update({f"해지 줄: {k}": v for k, v in ended_counts.items()})
    stats.update({f"줄: {k}": v for k, v in counts.items()})


def main(whole_market: bool):
    engine = init_db()
    stats = Counter()
    with session(engine) as db:
        resolver = PartyResolver(db)
        if whole_market:
            by_code = market_supply_filings()
            companies = db.scalars(select(Company).where(Company.corp_code.in_(list(by_code)))).all()
            stats["원장에 없는 회사의 공시 (건너뜀)"] = sum(
                len(rows) for code, rows in by_code.items() if code not in {c.corp_code for c in companies})
            jobs = [(c, by_code[c.corp_code]) for c in companies]
        else:
            companies = db.scalars(select(Company).where(Company.in_scope, Company.corp_code.is_not(None))).all()
            jobs = [(c, supply_filings(c.corp_code)) for c in companies]
        print(f"회사 {len(jobs)}곳, 공시 {sum(len(f) for _, f in jobs)}건", flush=True)
        try:
            for n, (company, filings) in enumerate(jobs, 1):
                load_company(db, company, filings, resolver, stats)
                db.commit()
                if n % 100 == 0:
                    print(f"  {n}/{len(jobs)}곳, 오늘 DART 호출 {dart.calls_today()}건", flush=True)
        except dart.DailyBudgetExceeded as stop:
            db.rollback()
            print(f"중단: {stop}. 내일 같은 명령으로 이어 받는다")
    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")
    print(f"오늘 DART 호출 {dart.calls_today()}건")


if __name__ == "__main__":
    main(whole_market="--all" in sys.argv[1:])
