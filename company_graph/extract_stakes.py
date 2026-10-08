"""5단계: 사건형 관계 — 타법인 주식 및 출자증권 취득결정 / 처분결정.

"A가 B의 지분을 사기로(팔기로) 결정했다"는 사실을 넣는다. 지분 관계(equity)가 연말의 상태라면 이것은 그 사이의 사건이다.
주체 = 공시를 낸 회사, 상대 = 발행회사, 수치 = 취득·처분 금액(원), 기준일 = 이사회결의일.

공시 목록은 extract_supply 가 받아 둔 전 시장 수시공시 목록(캐시)을 같이 쓴다.
실행: python -m company_graph.extract_stakes          수집 대상 기업이 낸 공시만
      python -m company_graph.extract_stakes --all    원장에 있는 회사 전부 (2024-01 이후 약 3,000건)
"""
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime

from sqlalchemy import delete, select

from . import dart, loader
from .config import CACHE_DIR
from .db import Company, Document, QualityLog, Relation, init_db, session
from .extract_equity import alias_index, link_in_context, own_affiliates, rcept_date
from .names import clean_reported, normalize
from .stake_parser import StakeDecision, original_filing_date, parse, withdrawal_notes

VERSION = "stake-4"  # 4: 주식 수를 담음. 3: 철회 내용을 줄로 남김, 대상 이름을 고친 정정을 이음.
# stake-2  # 2: 주석이 "1. 발행회사..."로 시작하는 공시를 읽음, 비율이 맞지 않는 줄에 표시, 철회 처리
REPORTS = {"타법인주식및출자증권취득결정": "acquisition", "타법인주식및출자증권처분결정": "disposal"}
REL_TYPE = {"acquisition": "stake_acquisition", "disposal": "stake_disposal"}


def stake_filings() -> dict[str, list[tuple[dict, str]]]:
    """캐시에 있는 수시공시 목록에서 취득·처분 결정만 회사별로."""
    by_company: dict[str, list[tuple[dict, str]]] = defaultdict(list)
    for path in sorted((CACHE_DIR / "dart_filings_I001_market").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            name = re.sub(r"^\[[^\]]+\]", "", re.sub(r"\s+", "", row["report_nm"]))
            for prefix, action in REPORTS.items():
                if name.startswith(prefix):
                    by_company[row["corp_code"]].append((row, action))
    return {code: sorted(rows, key=lambda x: x[0]["rcept_no"]) for code, rows in by_company.items()}


def ratio_matches(d: StakeDecision) -> bool | None:
    """금액 ÷ 자기자본이 공시에 적힌 비율과 맞는가 (적힌 비율은 반올림했거나 버린 구간으로 본다)."""
    if not (d.amount and d.equity and d.equity_ratio is not None):
        return None
    unit = type(d.equity_ratio)(1).scaleb(d.equity_ratio.as_tuple().exponent)
    computed = d.amount / d.equity * 100
    return d.equity_ratio - unit / 2 <= computed < d.equity_ratio + unit


def load_company(db, company: Company, filings: list[tuple[dict, str]], index, stats: Counter):
    affiliates = own_affiliates(db, company)
    db.execute(delete(QualityLog).where(QualityLog.rcept_no.in_([f["rcept_no"] for f, _ in filings])))
    failed_before = stats["원문을 받지 못함"]
    loaded: list[tuple[Document, Relation, StakeDecision]] = []
    withdrawals: list[tuple[Document, str, str | None, date | None, dict]] = []
    for filing, action in filings:
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
                           rcept_dt=rcept_date(rcept_no), doc_type="event", fetched_at=datetime.now())
            db.add(doc)
        doc.is_correction, doc.is_latest, doc.corrects_rcept_no = "정정]" in report_nm, True, None
        doc.content_sha256 = hashlib.sha256(raw).hexdigest()
        stats["공시"] += 1
        decision = parse(raw, action)
        if "철회" in report_nm:
            # 결정을 거둬들인 공시. 새 줄을 만들지 않고 아래에서 앞선 결정을 무효로 돌린다. 본문을 비워 내는 회사가 많다
            stats["철회 공시"] += 1
            withdrawals.append((doc, action, decision.target if decision else None, original_filing_date(raw),
                                withdrawal_notes(raw)))
            continue
        if decision is None or not decision.target:
            stats["양식을 읽지 못함"] += 1
            db.add(QualityLog(check_name="range", rcept_no=rcept_no, created_at=datetime.now(),
                              detail=f"{company.name} {report_nm}: 취득·처분 결정 양식을 찾지 못함"))
            continue
        check = ratio_matches(decision)
        if check is False:
            stats["금액÷자기자본이 적힌 비율과 다름"] += 1
            db.add(QualityLog(check_name="cross_check", rcept_no=rcept_no, created_at=datetime.now(),
                              detail=f"{company.name}: {decision.amount} ÷ {decision.equity} ≠ {decision.equity_ratio}%"))
        elif check:
            stats["금액÷자기자본이 적힌 비율과 맞음"] += 1
        target_id, reason = link_in_context(index, affiliates, decision.target)
        stats[f"대상 회사를 연결 ({reason})" if target_id else "대상 회사가 원장에 없음"] += 1
        stats["취득 결정" if action == "acquisition" else "처분 결정"] += 1
        attrs = {"link_reason": reason, "nationality": decision.nationality, "relation": decision.relation,
                 "business": decision.business, "pct_after": None if decision.pct_after is None else str(decision.pct_after),
                 "equity_ratio_pct": None if decision.equity_ratio is None else str(decision.equity_ratio),
                 "shares": None if decision.shares is None else str(int(decision.shares)),
                 "shares_after": None if decision.shares_after is None else str(int(decision.shares_after)),
                 "method": decision.method, "purpose": decision.purpose, "subsidiary": decision.subsidiary,
                 "expected_date": decision.expected_date and decision.expected_date.isoformat(),
                 "correction_reason": decision.correction_reason,
                 # 공시에 적힌 금액·자기자본·비율이 서로 맞지 않는다. 적힌 대로 넣되 쓰는 쪽이 알 수 있게 표시한다
                 "ratio_check": "mismatch" if check is False else None,
                 "changes": [list(ch) for ch in decision.changes] or None}
        relation = Relation(
            subject_company_id=company.company_id, object_company_id=target_id, object_name_raw=decision.target[:300],
            rel_type=REL_TYPE[action], value_num=decision.amount, value_unit="krw" if decision.amount else None,
            as_of_date=decision.decision_date, disclosed_date=doc.rcept_dt, rcept_no=rcept_no,
            extract_method="rule", trust_tier=1, attrs={k: v for k, v in attrs.items() if v is not None})
        loaded.append((doc, relation, decision))

    # 정정 처리: 같은 결정(종류·대상·결의일이 같음)의 공시를 접수 순으로 잇는다
    chains: dict[tuple, list[tuple[Document, Relation, StakeDecision]]] = defaultdict(list)
    for item in loaded:
        doc, relation, decision = item
        key = (decision.action, normalize(clean_reported(decision.target)), decision.decision_date)
        previous = chains[key][-1] if chains[key] else None
        if previous is None and doc.is_correction:
            # 결의일이 정정으로 바뀐 경우: 원래 공시 제출일과 대상이 같은 공시가 하나뿐이면 잇는다.
            # 원래 공시 제출일을 읽지 못했으면 앞선 공시 전부에서 찾는다
            filed_then = [x for x in loaded if x[0].rcept_no < doc.rcept_no and x[0].is_latest and x[2].action == decision.action
                          and decision.original_date in (None, x[0].rcept_dt)]
            same_day = [x for x in filed_then if normalize(clean_reported(x[2].target)) == key[1]]
            if not same_day:
                # 대상 회사 이름을 고친 정정(예: "티케이이엔에" → "티케이이엔에스"): 금액과 결의일이 같은 공시가 하나뿐이면 잇는다
                same_day = [x for x in filed_then if x[2].amount is not None and x[2].amount == decision.amount
                            and x[2].decision_date == decision.decision_date]
            previous = same_day[0] if len(same_day) == 1 else None
        if previous:
            doc.corrects_rcept_no = previous[0].rcept_no
            previous[0].is_latest = False
            previous[1].invalidated_date = doc.rcept_dt
            stats["정정으로 무효가 된 줄"] += 1
        elif doc.is_correction:
            stats["정정인데 원래 공시를 못 찾음"] += 1
            db.add(QualityLog(check_name="correction", rcept_no=doc.rcept_no, created_at=datetime.now(),
                              detail=f"{company.name}: 원래 공시({decision.original_date})를 찾지 못함"))
        chains[key].append(item)

    docs = {x[0].rcept_no: x[0] for x in loaded}

    def first_filed(doc: Document) -> date:
        """정정을 거슬러 올라가 맨 처음 공시한 날."""
        while doc.corrects_rcept_no in docs:
            doc = docs[doc.corrects_rcept_no]
        return doc.rcept_dt

    withdrawn_rows: list[Relation] = []
    for doc, action, target, original_date, notes in withdrawals:
        earlier = [x for x in loaded if x[2].action == action and x[0].rcept_no < doc.rcept_no and x[0].is_latest]
        if target and len(earlier) > 1:
            key = normalize(clean_reported(target))
            earlier = [x for x in earlier if normalize(clean_reported(x[2].target)) == key] or earlier
        if original_date and len(earlier) > 1:
            earlier = [x for x in earlier if original_date in (x[0].rcept_dt, first_filed(x[0]))] or earlier
        # 철회 사실도 한 줄로 남긴다. 무효일을 공개일과 같게 두어 "유효한 결정"으로는 조회되지 않고, 공시 목록에서만 내용이 보인다
        found = earlier[0] if len(earlier) == 1 else None
        attrs = {"withdrawn": True, "reason": notes["reason"], "before_withdrawal": notes["before"],
                 "original_filed": original_date and original_date.isoformat(),
                 "withdrawn_filing": found[0].rcept_no if found else None}
        # 철회 공시의 "정정전" 칸에는 금액이 여럿(취득금액, 자기자본 등) 섞여 있어 어느 것이 취득금액인지 가릴 수 없다.
        # 원래 결정을 찾았을 때만 그 금액을 쓰고, 못 찾았으면 비워 두고 before_withdrawal 에 적힌 그대로를 남긴다
        amount = found[1].value_num if found else None
        withdrawn_rows.append(Relation(
            subject_company_id=company.company_id, object_company_id=found[1].object_company_id if found else None,
            object_name_raw=(target or (found[1].object_name_raw if found else None) or "(철회 공시에 대상 회사가 적혀 있지 않음)")[:300],
            rel_type=REL_TYPE[action], value_num=amount, value_unit="krw" if amount else None,
            as_of_date=None, disclosed_date=doc.rcept_dt, invalidated_date=doc.rcept_dt, rcept_no=doc.rcept_no,
            extract_method="rule", trust_tier=1, attrs={k: v for k, v in attrs.items() if v is not None}))
        if len(earlier) == 1:
            doc.corrects_rcept_no = earlier[0][0].rcept_no
            earlier[0][0].is_latest = False
            earlier[0][1].invalidated_date = doc.rcept_dt
            stats["철회로 무효가 된 줄"] += 1
        else:
            stats["철회인데 거둬들인 결정을 못 찾음"] += 1
            db.add(QualityLog(check_name="correction", rcept_no=doc.rcept_no, created_at=datetime.now(),
                              detail=f"{company.name}: 철회한 결정을 찾지 못함 (후보 {len(earlier)}건)"))

    if stats["원문을 받지 못함"] > failed_before:
        stats["원문 실패로 줄 맞추기를 건너뛴 회사"] += 1
        return
    counts = loader.sync(db, [Relation.rel_type.in_(list(REL_TYPE.values())),
                              Relation.subject_company_id == company.company_id],
                         [relation for _, relation, _ in loaded] + withdrawn_rows, VERSION)
    stats.update({f"줄: {k}": v for k, v in counts.items()})


def main(everyone: bool):
    engine = init_db()
    stats = Counter()
    by_code = stake_filings()
    with session(engine) as db:
        index = alias_index(db)
        scope = [] if everyone else [Company.in_scope]
        companies = db.scalars(select(Company).where(Company.corp_code.in_(list(by_code)), *scope)).all()
        print(f"회사 {len(companies)}곳, 공시 {sum(len(by_code[c.corp_code]) for c in companies)}건", flush=True)
        try:
            for n, company in enumerate(companies, 1):
                load_company(db, company, by_code[company.corp_code], index, stats)
                db.commit()
                if n % 100 == 0:
                    print(f"  {n}/{len(companies)}곳, 오늘 DART 호출 {dart.calls_today()}건", flush=True)
        except dart.DailyBudgetExceeded as stop:
            db.rollback()
            print(f"중단: {stop}. 내일 같은 명령으로 이어 받는다")
    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")
    print(f"오늘 DART 호출 {dart.calls_today()}건")


if __name__ == "__main__":
    main(everyone="--all" in sys.argv[1:])
