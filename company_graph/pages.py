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
    """화면이 돌려보낸 설계도(답을 페이지로 남기기). 서버가 붙인 서명이 맞아야 한다"""
    expected = sign(body) if isinstance(body, dict) else None
    if not expected or not isinstance(sig, str) or not hmac.compare_digest(expected, sig):
        raise PageError("이 답은 페이지로 남길 수 없습니다 (서버가 만든 답이 아니거나 바뀌었습니다)", 403)
    return store(db, body, visitor, owner)


def store(db, body: dict, visitor: str, owner: bool = False, title: str | None = None) -> dict:
    """설계도를 저장한다. 서버 안에서 만든 설계도(Agent의 save_page)는 서명 없이 바로 여기로 온다"""
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
    title = (title or body["question"])
    title = title[:TITLE_MAX] + ("…" if len(title) > TITLE_MAX else "")
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


# ---------- Agent가 직접 짜는 페이지 (save_page, 기본으로 켬. ASK_PAGES=0 이면 끔) ----------

TOOL = "save_page"
SKILL = "make-page"
AGENT_TEXT_MAX = 600

# 세어서 하는 말. 평가 14판에서 틀린 문장 셋이 모두 이것이었다("공급계약은 34건", "그 뒤 보고는 없습니다").
# 숫자 검사는 그 숫자가 조회 결과 어딘가에 있는지만 보므로, 무엇을 센 수인지는 가리지 못한다. 세는 일은 카드(서버)가 한다
_COUNT = re.compile(r"(?<![\d.])\d[\d,]*\s*(?:건|곳|쌍|차례|명|개(?!월))|(?<![가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:건|곳|쌍|차례)")
_NONE_AFTER = re.compile(r"(?:그\s*뒤|그\s*후|그\s*이후|이후로?|더\s*이상|더는|아직|이\s*밖에|그\s*밖에|외에는?)[^.\n]{0,40}?(?:없습니다|없다|없음|없었|않았|않습니다)")
_ONLY = re.compile(r"유일한|유일하게|단\s*한\s*(?:건|번|곳)|뿐입니다")
_ABOUT_DB = re.compile(r"DB|알 수 없|모으지 않|담지 않|수집하지 않")


def count_claims(text: str) -> list[str]:
    """글에서 개수, '그 뒤 없다', '유일하다' 처럼 세어서 하는 문장.
    이 DB가 무엇을 모으지 않는지 밝히는 말('그 밖의 것은 이 DB에 없습니다')은 '없다'로 세지 않는다. 개수는 그런 문장에서도 본다.
    평가 15판 P20: 'DB' 가 든 문장을 통째로 건너뛰어 '합계 2건입니다(… 이 DB에 없음)' 가 남았다"""
    out = []
    for sentence in re.split(r"(?<=[.!?。])\s+|\n+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        # DB를 밝히는 괄호는 떼고 본다. 괄호 밖에도 DB를 밝히는 말이 있으면 '없다·유일하다' 검사만 건너뛴다
        bare = re.sub(r"\([^()]*\)", lambda m: "" if _ABOUT_DB.search(m.group()) else m.group(), sentence)
        counted = _COUNT.search(RECEIPT.sub("", bare))
        if counted or (not _ABOUT_DB.search(bare) and (_NONE_AFTER.search(bare) or _ONLY.search(bare))):
            out.append(sentence)
    return out


def _without(text: str, claims: list[str]) -> str:
    for sentence in claims:
        text = text.replace(sentence, "")
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def enabled() -> bool:
    """기본으로 켠다(평가 16판에서 기준 8개를 채움). save_page 도구와 make-page 규칙이 함께 생기고, ASK_PAGES=0 이면 둘 다 없다"""
    return os.environ.get("ASK_PAGES", "1") == "1"


def tool() -> dict:
    return {
        "name": TOOL,
        "description": "사용자가 페이지로 만들어 달라고 할 때, 조회를 마친 뒤 페이지의 설계도를 저장합니다. 규칙은 read_skill 로 make-page 를 읽습니다. "
                       "블록은 위에서부터 차례로 그려집니다. view 블록은 카드의 종류와 대상만 주면 숫자는 서버가 DB에서 채웁니다. "
                       "text 블록은 600자 안의 짧은 글이며, 적은 접수번호와 숫자는 이번 조회 결과에 있어야 합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "페이지 제목, 40자 안팎"},
                "blocks": {"type": "array", "minItems": 2, "maxItems": BLOCKS_MAX, "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["text", "view"]},
                        "text": {"type": "string", "description": "type=text 일 때의 글"},
                        "kind": {"type": "string", "enum": list(KINDS), "description": "type=view 일 때 카드 종류 (show_on_graph 의 views 와 같음)"},
                        "company_id": {"type": "integer"},
                        "other_company_id": {"type": "integer", "description": "path, compare 일 때 상대 기업"},
                        "since": {"type": "string", "description": "기간이 있는 카드의 시작일 YYYY-MM-DD"},
                    },
                    "required": ["type"],
                }},
            },
            "required": ["title", "blocks"],
        },
    }


def propose(db, arguments: dict, when: date, seen: set[str], bodies: list[str], question: str, warned: dict) -> dict:
    """Agent가 낸 설계도를 검사한다. 고칠 것이 있으면 error 로 돌려줘 다시 부르게 하고, 맞으면 _page 에 설계도를 담는다.
    저장은 답이 끝난 뒤 서버(api._answered)가 한다. 평가처럼 서버를 거치지 않으면 저장하지 않는다.
    warned: 숫자 검사에 한 번 걸렸는지 (한 번만 고치게 하고, 두 번째에는 받는다. 답의 숫자 검사와 같은 방식)"""
    from . import agent_tools, verify, wording

    title = " ".join(str(arguments.get("title") or "").split())
    raw = arguments.get("blocks")
    problems = []
    if not title:
        problems.append("title 이 비었습니다")
    if not isinstance(raw, list) or not raw:
        return {"error": "blocks 에 블록을 하나 이상 주세요"}
    blocks, cards = [], []
    for i, block in enumerate(raw[:BLOCKS_MAX], 1):
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text":
            text = str(block.get("text") or "").strip()
            if not text:
                problems.append(f"{i}번 글 블록이 비었습니다")
            elif len(text) > AGENT_TEXT_MAX:
                problems.append(f"{i}번 글 블록이 {len(text)}자입니다. {AGENT_TEXT_MAX}자 안으로 줄이고 숫자는 카드에 맡기세요")
            blocks.append({"type": "text", "text": text})
        elif kind == "view":
            arguments_ = {k: block[k] for k in ("kind", "company_id", "other_company_id", "since") if block.get(k) is not None}
            card = agent_tools.call(db, "show_view", {**arguments_, "as_of": when.isoformat()})
            if "error" in card:
                problems.append(f"{i}번 카드({block.get('kind')}): {card['error']}")
            else:
                cards.append({"block": i, "shown": card["shown"], "summary": card.get("summary")})
            blocks.append({"type": "view", **arguments_, "as_of": when.isoformat()})
        else:
            problems.append(f"{i}번 블록의 type 은 text 나 view 입니다")
    if len(raw) > BLOCKS_MAX:
        problems.append(f"블록은 {BLOCKS_MAX}개까지입니다")
    if not cards and not problems:
        problems.append("카드(view)를 하나 이상 넣으세요. 숫자는 카드가 담습니다")
    if sum(b["type"] == "view" for b in blocks) > VIEWS_MAX:
        problems.append(f"카드는 {VIEWS_MAX}장까지입니다")
    text = "\n\n".join(b["text"] for b in blocks if b["type"] == "text")
    unknown = sorted(set(RECEIPT.findall(text)) - seen)
    if unknown:
        problems.append(f"이번 조회 결과에 없는 접수번호: {', '.join(unknown)}")
    flagged = wording.flagged(text, question)
    if flagged:
        problems.append("쓰지 않기로 한 표현: " + "; ".join(flagged[:5]))
    numbers = verify.unsupported(text, bodies, when, question) if text else []
    if numbers and not warned.get("numbers"):
        warned["numbers"] = True
        problems.append("조회 결과에서 확인되지 않는 값: " + ", ".join(numbers[:8]) + ". 결과에 있는 값으로 고치거나 빼고 카드에 맡기세요")
    # 세어서 하는 말은 한 번 돌려보내고, 두 번째에도 남으면 그 문장만 지우고 받는다(페이지에 남지 않는다)
    claims = count_claims(text) if text else []
    if claims and not warned.get("counts"):
        warned["counts"] = True
        problems.append("개수나 '그 뒤 없다', '유일하다' 같은 세어서 하는 말은 글에 쓰지 않습니다(세는 일은 카드가 합니다). 이 문장을 빼거나 세지 않는 말로 고치세요: "
                        + " | ".join(c[:80] for c in claims[:5]))
    if problems:
        return {"error": "페이지를 저장하지 않았습니다. 고쳐서 다시 부르세요: " + " / ".join(problems)}
    removed = []
    if claims:
        removed = claims
        blocks = [b if b["type"] != "text" else {**b, "text": _without(b["text"], claims)} for b in blocks]
        blocks = [b for b in blocks if b["type"] != "text" or b["text"]]
    return {"saved": True, "title": title, "cards": cards,
            **({"removed": removed} if removed else {}),
            "note": "페이지 설계도를 받았습니다. 답이 끝나면 화면이 페이지 주소를 붙입니다. 답에는 페이지에 무엇을 담았는지 두세 문장으로만 적고, 주소는 적지 않습니다",
            "_page": {"title": title, "question": " ".join(question.split())[:300], "as_of": when.isoformat(), "blocks": blocks,
                      **({"unverified": numbers} if numbers else {}), **({"removed_counts": removed} if removed else {})}}
