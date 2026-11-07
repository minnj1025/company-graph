"""뉴스 분석 뒤에 주인공이 낸 공시: 뉴스가 전한 일을 공시가 뒤따라 확인했는지 보는 자리.

분석은 뉴스 전날(조회 시점)까지 나온 공시만 본다. 그 뒤 매일 아침 받는 공시 가운데 주인공의
공급계약·해지, 지분 취득·처분 결정, 증자·사채·자기주식 결정을 모은다.
뉴스 제목에 금액이 있으면 공시 금액과 견준다(3% 안쪽이면 같다고 본다). 내용이 같은 일인지는 사람이 원문으로 확인한다.
"""
import re
from datetime import date, timedelta

from sqlalchemy import func, select

from .capital import LABELS as CAPITAL_LABELS
from .db import CapitalEvent, Document, Relation

DART_LINK = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
SAME = 0.03     # 금액이 이만큼 안쪽으로 다르면 뉴스와 같은 금액으로 본다
WINDOW = 60     # 뉴스 날부터 며칠 안의 공시를 볼지 (오래된 뉴스에 상관없는 계약이 쌓이지 않게)
DOC_TYPES = ("supply_contract", "event")
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억)(?:\s*(\d[\d,]*)\s*억)?")


def title_amounts(title: str) -> list[int]:
    """뉴스 제목의 금액(원): "6301억" → 630,100,000,000, "2.7조" → 2,700,000,000,000, "1조 2000억" → 1,200,000,000,000."""
    out = []
    for number, unit, more in _AMOUNT.findall(title or ""):
        value = float(number.replace(",", "")) * (1e12 if unit == "조" else 1e8)
        if unit == "조" and more:
            value += float(more.replace(",", "")) * 1e8
        out.append(int(value))
    return out


def same_amount(amount: int | None, news: list[int]) -> bool:
    return bool(amount) and any(abs(amount - n) <= n * SAME for n in news if n > 0)


def _kind(report: str) -> str:
    plain = re.sub(r"\[[^\]]*\]", "", report).replace(" ", "")
    for word, label in (("공급계약해지", "공급계약 해지"), ("공급계약", "공급계약"), ("타법인주식및출자증권취득", "지분 취득 결정"),
                        ("타법인주식및출자증권처분", "지분 처분 결정")):
        if word in plain:
            return label
    return plain[:14]


def checked_through(db) -> date | None:
    """매일 받는 공시를 어느 날까지 받아 두었는지."""
    days = [db.scalar(select(func.max(Document.rcept_dt)).where(Document.doc_type == "supply_contract")),
            db.scalar(select(func.max(CapitalEvent.report_date)))]
    days = [d for d in days if d]
    return max(days) if days else None


def filings_after(db, company_ids: list[int], after: date, until: date | None = None) -> list[dict]:
    """after 다음 날부터 WINDOW 일 안에 company_ids 가 낸 공시 (날짜 순). 회사별로 company_id 를 붙인다."""
    if not company_ids:
        return []
    until = until or after + timedelta(days=WINDOW)
    docs = db.scalars(select(Document).where(Document.company_id.in_(company_ids), Document.rcept_dt > after, Document.rcept_dt <= until,
                                             Document.doc_type.in_(DOC_TYPES))).all()
    edges = {}
    if docs:
        for r in db.scalars(select(Relation).where(Relation.rcept_no.in_([d.rcept_no for d in docs]))):
            edges.setdefault(r.rcept_no, r)
    out = []
    for d in docs:
        edge = edges.get(d.rcept_no)
        amount = int(edge.value_num) if edge is not None and edge.value_unit == "krw" and edge.value_num else None
        out.append({"company_id": d.company_id, "rcept_no": d.rcept_no, "filed": d.rcept_dt.isoformat(), "kind": _kind(d.report_nm),
                    "report": d.report_nm, "amount": amount, "party": edge.object_name_raw if edge is not None else None,
                    "what": (edge.attrs or {}).get("title") if edge is not None else None, "corrected": "정정" in d.report_nm,
                    "url": DART_LINK + d.rcept_no})
    seen = {row["rcept_no"] for row in out}
    for c in db.scalars(select(CapitalEvent).where(CapitalEvent.company_id.in_(company_ids), CapitalEvent.report_date > after,
                                                   CapitalEvent.report_date <= until)):
        if c.rcept_no in seen:
            continue
        seen.add(c.rcept_no)
        out.append({"company_id": c.company_id, "rcept_no": c.rcept_no, "filed": c.report_date.isoformat(), "kind": CAPITAL_LABELS.get(c.kind, c.kind),
                    "report": CAPITAL_LABELS.get(c.kind, c.kind) + " 결정", "amount": c.amount, "party": None, "what": c.purpose,
                    "corrected": False, "url": DART_LINK + c.rcept_no})
    return sorted(out, key=lambda row: (row["filed"], row["rcept_no"]))


def summarize(rows: list[dict], title: str) -> dict:
    news = title_amounts(title)
    for row in rows:
        row["same_amount"] = same_amount(row["amount"], news)
    return {"count": len(rows), "same_amount": sum(row["same_amount"] for row in rows),
            "contracts": sum(row["kind"] == "공급계약" for row in rows), "news_amounts": news}


def memo_followups(db, after: date, company_ids: list[int], title: str = "") -> dict:
    rows = filings_after(db, company_ids, after)
    through = checked_through(db)
    return {"after": after.isoformat(), "until": (after + timedelta(days=WINDOW)).isoformat(), "checked_through": through.isoformat() if through else None,
            "filings": rows, **summarize(rows, title)}
