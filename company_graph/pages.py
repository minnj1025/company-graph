"""분석 페이지: Agent의 답을 주소가 있는 페이지로 남긴다.

설계도(spec)만 저장한다. 글은 답의 글 그대로이고, 카드는 종류와 대상만 둔다. 카드의 숫자는 페이지를 열 때마다
지금의 카드 코드(views.build)가 DB에서 채운다. 그래서 모델이 숫자를 옮겨 적을 자리가 없고, 페이지를 열 때 Claude를 부르지 않는다.

- 기본은 만든 날 기준으로 카드를 채운다(글과 카드가 늘 맞는다). basis="today" 면 카드만 오늘 기준이다.
- 만든 뒤 그 기업들에 새로 들어온 공시는 코드가 센다(newer).
- 아무 글이나 Corpath 주소에 올리지 못하게, 서버가 답을 낼 때 붙인 서명(sig)이 맞는 설계도만 받는다.
  서명 열쇠는 서버의 비밀 환경 변수에서 만든다(PAGE_SECRET, 없으면 ASK_OWNER_KEY 또는 ANTHROPIC_API_KEY).
- 공개 목록은 없다. 주소(slug)를 아는 사람만 연다. 지우기 열쇠는 만든 사람의 브라우저에만 있다.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import date, datetime

from sqlalchemy import func, select

from .db import Base, CapitalEvent, Company, Document, InsiderReport, MajorHolding, UserPage
from .views import KINDS

TEXT_MAX = 8000         # 답 하나의 글
BLOCKS_MAX = 12
VIEWS_MAX = 6
COMPANIES_MAX = 40
TITLE_MAX = 80
VISITOR_LIMIT = int(os.environ.get("PAGE_VISITOR_LIMIT", "5"))    # 한 사람이 하루에 만드는 페이지
DAILY_LIMIT = int(os.environ.get("PAGE_DAILY_LIMIT", "200"))      # 사이트 전체로 하루에
SLUG = re.compile(r"^[A-Za-z0-9_-]{10}$")
RECEIPT = re.compile(r"(?<!\d)(20\d{12})(?!\d)")
VIEW_KEYS = ("kind", "company_id", "other_company_id", "since", "as_of")


class PageError(Exception):
    """방문자에게 그대로 보여 줄 수 있는 거절. status 는 HTTP 상태"""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _secret() -> bytes | None:
    value = os.environ.get("PAGE_SECRET") or os.environ.get("ASK_OWNER_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    return hashlib.sha256(b"corpath-page:" + value.encode()).digest() if value else None


def _canonical(draft: dict) -> bytes:
    return json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sign(draft: dict) -> str | None:
    key = _secret()
    return hmac.new(key, _canonical(draft), hashlib.sha256).hexdigest() if key else None


def view_spec(spec: dict) -> dict:
    """카드 하나를 다시 만들 때 필요한 것만 남긴다 (agent_tools.show_on_graph 가 카드에 붙인다)"""
    return {k: spec[k] for k in VIEW_KEYS if spec.get(k) is not None}


def draft(question: str, result: dict, views: list[dict], companies: list[dict], highlight: list[int], calls: int) -> dict | None:
    """답 하나를 페이지로 저장할 수 있으면 설계도와 서명을 돌려준다. 저장하면 안 되는 답이면 None.
    조회 없이 한 답(거절 등), 조회 결과에 없는 접수번호가 있는 답, 금지 표현이 남은 답은 남기지 않는다."""
    answer = (result.get("answer") or "").strip()
    if not answer or calls == 0 or result.get("cited_not_in_results") or (result.get("wording") or {}).get("left"):
        return None
    blocks = [{"type": "view", **view_spec(v["spec"])} for v in views if v.get("spec")][:VIEWS_MAX]
    body = {"question": " ".join(question.split())[:300], "as_of": result["as_of"],
            "blocks": blocks + [{"type": "text", "text": answer[:TEXT_MAX]}],
            "companies": [{"id": int(c["id"]), "name": str(c["name"])} for c in companies][:COMPANIES_MAX],
            "highlight": [int(i) for i in highlight][:12]}
    signature = sign(body)
    return {"draft": body, "sig": signature} if signature else None


def _check(body: dict) -> dict:
    """서명이 맞아도 모양을 한 번 더 본다 (서명 열쇠를 바꾸기 전의 설계도, 코드가 바뀐 뒤의 설계도)"""
    if not isinstance(body, dict) or not isinstance(body.get("blocks"), list):
        raise PageError("페이지 설계도의 모양이 맞지 않습니다")
    date.fromisoformat(str(body.get("as_of")))
    blocks = body["blocks"]
    views = [b for b in blocks if b.get("type") == "view"]
    if not blocks or len(blocks) > BLOCKS_MAX or len(views) > VIEWS_MAX:
        raise PageError("블록이 없거나 너무 많습니다")
    for block in blocks:
        if block.get("type") == "text":
            if not isinstance(block.get("text"), str) or len(block["text"]) > TEXT_MAX:
                raise PageError("글 블록이 맞지 않습니다")
        elif block.get("type") == "view":
            if block.get("kind") not in KINDS or not isinstance(block.get("company_id"), int):
                raise PageError("카드 블록이 맞지 않습니다")
        else:
            raise PageError("모르는 블록입니다")
    return body


_ready = False


def ensure_table(engine):
    """운영 DB에 표가 없으면 만든다. 프로세스마다 한 번만 본다"""
    global _ready
    if not _ready:
        Base.metadata.create_all(engine, tables=[UserPage.__table__])
        _ready = True


def create(db, body: dict, sig: str, visitor: str, owner: bool = False) -> dict:
    expected = sign(body) if isinstance(body, dict) else None
    if not expected or not isinstance(sig, str) or not hmac.compare_digest(expected, sig):
        raise PageError("이 답은 페이지로 남길 수 없습니다 (서버가 만든 답이 아니거나 바뀌었습니다)", 403)
    body = _check(body)
    if not owner:
        today = datetime.combine(date.today(), datetime.min.time())
        mine = db.scalar(select(func.count()).select_from(UserPage).where(UserPage.visitor == visitor, UserPage.created_at >= today))
        if mine >= VISITOR_LIMIT:
            raise PageError(f"한 사람이 하루에 만들 수 있는 페이지({VISITOR_LIMIT}개)를 다 만들었습니다", 429)
        if db.scalar(select(func.count()).select_from(UserPage).where(UserPage.created_at >= today)) >= DAILY_LIMIT:
            raise PageError("오늘 만들 수 있는 페이지를 다 만들었습니다. 내일 다시 해 주세요", 429)
    key = secrets.token_urlsafe(18)
    slug = secrets.token_urlsafe(8)[:10]
    while db.get(UserPage, slug) is not None:
        slug = secrets.token_urlsafe(8)[:10]
    title = body["question"][:TITLE_MAX] + ("…" if len(body["question"]) > TITLE_MAX else "")
    db.add(UserPage(slug=slug, edit_hash=hashlib.sha256(key.encode()).hexdigest(), visitor=visitor, title=title,
                    as_of=date.fromisoformat(body["as_of"]), spec=body, created_at=datetime.now()))
    db.commit()
    return {"slug": slug, "edit_key": key, "title": title}


def _page(db, slug: str) -> UserPage:
    page = db.get(UserPage, slug) if SLUG.match(slug or "") else None
    if page is None:
        raise PageError("페이지가 없습니다. 주소를 확인하거나, 만든 사람이 지웠을 수 있습니다", 404)
    return page


def _newer(db, ids: list[int], after: date, until: date) -> dict:
    """만든 날 뒤에 그 기업들에 들어온 공시 (이 DB가 모으는 것만)"""
    if not ids or until <= after:
        return {"total": 0, "items": []}
    names = {c.company_id: c.label for c in db.scalars(select(Company).where(Company.company_id.in_(ids)))}
    items = [(d.rcept_dt, d.rcept_no, d.company_id, d.report_nm.strip()) for d in db.scalars(
        select(Document).where(Document.company_id.in_(ids), Document.rcept_dt > after, Document.rcept_dt <= until))]
    for model, label in ((MajorHolding, "주식등의 대량보유상황보고"), (InsiderReport, "임원·주요주주 특정증권등 소유상황보고"),
                         (CapitalEvent, None)):
        for row in db.scalars(select(model).where(model.company_id.in_(ids), model.report_date > after, model.report_date <= until)):
            if label is None:
                from .capital import LABELS
                text = f"{LABELS.get(row.kind, row.kind)} 결정"
            else:
                text = label
            items.append((row.report_date, row.rcept_no, row.company_id, text))
    unique = {}
    for item in sorted(items, reverse=True):
        unique.setdefault(item[1], item)
    ordered = list(unique.values())
    return {"total": len(ordered), "items": [{"filed": d.isoformat(), "rcept_no": no, "company": names.get(cid), "report": text}
                                             for d, no, cid, text in ordered[:8]]}


def read(db, slug: str, basis: str = "made", today: date | None = None) -> dict:
    """페이지 하나를 그린다. 카드는 여기서 DB로 채운다(Claude를 부르지 않는다)"""
    from . import agent_tools, query

    page = _page(db, slug)
    spec = page.spec
    today = today or date.today()
    basis = "today" if basis == "today" else "made"
    blocks = []
    for block in spec["blocks"]:
        if block["type"] == "text":
            blocks.append({"type": "text", "text": block["text"]})
            continue
        when = today.isoformat() if basis == "today" else (block.get("as_of") or page.as_of.isoformat())
        arguments = {**{k: block[k] for k in ("kind", "company_id", "other_company_id", "since") if block.get(k) is not None}, "as_of": when}
        card = agent_tools.call(db, "show_view", arguments)
        blocks.append({"type": "view", "view": card["_view"]} if "_view" in card else {"type": "view", "kind": block["kind"], "error": card.get("error")})
    text = " ".join(b["text"] for b in spec["blocks"] if b["type"] == "text")
    cited = list(dict.fromkeys(RECEIPT.findall(text)))[:40]
    documents = {d.rcept_no: d for d in db.scalars(select(Document).where(Document.rcept_no.in_(cited)))} if cited else {}
    names = {c.company_id: c.label for c in db.scalars(select(Company).where(Company.company_id.in_({d.company_id for d in documents.values()})))} if documents else {}
    sources = [{"rcept_no": no, "url": query.DART_VIEWER + no, "company": names.get(documents[no].company_id),
                "report": documents[no].report_nm.strip(), "filed": documents[no].rcept_dt.isoformat()}
               if no in documents else {"rcept_no": no, "url": query.DART_VIEWER + no, "company": None, "report": None, "filed": None}
               for no in cited]
    ids = list(dict.fromkeys([b["company_id"] for b in spec["blocks"] if b["type"] == "view"] + spec.get("highlight", [])))[:12]
    return {"slug": page.slug, "title": page.title, "question": spec["question"], "as_of": page.as_of.isoformat(),
            "basis": basis, "shown_as_of": page.as_of.isoformat() if basis == "made" else today.isoformat(),
            "created_at": page.created_at.isoformat(timespec="minutes"), "blocks": blocks,
            "companies": spec.get("companies", []), "highlight": spec.get("highlight", []), "sources": sources,
            "newer": _newer(db, ids, page.as_of, today)}


def delete(db, slug: str, key: str | None, owner: bool = False) -> None:
    page = _page(db, slug)
    if not owner and not (key and hmac.compare_digest(page.edit_hash, hashlib.sha256(key.encode()).hexdigest())):
        raise PageError("지우기 열쇠가 맞지 않습니다", 403)
    db.delete(page)
    db.commit()
