"""회사의 옛 이름을 별칭으로 넣는다. 이름을 바꾼 회사를 옛 이름으로 물어도 찾게 한다.

DART의 회사 목록(corpCode, 기업개황)과 공시 목록은 지금 이름만 준다. 공시 원문에는 공시한 때의 이름이 남아 있으므로,
이미 받아 둔 원문(data/cache/dart_document)에서 읽는다. DART를 부르지 않는다.
- 일반 공시: <COMPANY-NAME AREGCIK="고유번호">이름  → 고유번호로 회사를 정한다
- 거래소 공시(접수번호 9번째 자리 8): <title>이름/보고서명  → 접수번호로 document 표에서 회사를 정한다
이름이 이미 다른 회사의 별칭이거나 원문에서 여러 회사가 쓴 이름이면 넣지 않는다(옛 이름을 다른 회사가 이어 쓰거나 나눠 쓴 경우).

2026-11 평가 16판 P40: 'LIG넥스원'으로 찾지 못했다(원장 이름 LIG디펜스앤에어로스페이스, 별칭에 옛 이름이 없었다).

실행: python -m company_graph.former_names [days]   (days 를 주면 그 날수 안에 받은 원문만 본다. 매일 작업이 쓴다)
"""
import re
import sys
import time

from sqlalchemy import select

from .config import CACHE_DIR
from .db import Company, CompanyAlias, Document, init_db, session
from .names import clean_reported, normalize

DOCUMENTS = CACHE_DIR / "dart_document"
_HEAD = re.compile(rb'<COMPANY-NAME AREGCIK="(\d{8})">([^<]+)')
_TITLE = re.compile(rb"<title>([^<]+?)/")


def read(days: float | None = None) -> tuple[dict[str, set[str]], dict[str, str]]:
    """(고유번호 → 원문에 적힌 이름들, 거래소 공시 접수번호 → 제목의 이름)"""
    by_code: dict[str, set[str]] = {}
    by_receipt: dict[str, str] = {}
    since = time.time() - days * 86400 if days else 0
    for path in DOCUMENTS.glob("*.xml"):
        if since and path.stat().st_mtime < since:
            continue
        with open(path, "rb") as f:
            head = f.read(6000)
        found = _HEAD.search(head)
        if found:
            by_code.setdefault(found.group(1).decode(), set()).add(found.group(2).decode("utf-8", "replace").strip())
        elif (found := _TITLE.search(head)) and path.stem[8:9] == "8":
            by_receipt[path.stem] = found.group(1).decode("utf-8", "replace").strip()
    return by_code, by_receipt


def add(db, by_code: dict[str, set[str]], by_receipt: dict[str, str], dry: bool = False) -> list[tuple[str, str]]:
    """새로 넣은 (지금 이름, 별칭). dry 면 넣지 않고 무엇을 넣을지만 돌려준다"""
    companies = {c.corp_code: c for c in db.scalars(select(Company).where(Company.corp_code.is_not(None)))}
    owner = dict(db.execute(select(Document.rcept_no, Document.company_id).where(Document.rcept_no.like("________8%"))).all())
    names: dict[int, set[str]] = {}
    for code, found in by_code.items():
        if code in companies:
            names.setdefault(companies[code].company_id, set()).update(found)
    for rcept_no, name in by_receipt.items():
        if owner.get(rcept_no):
            names.setdefault(owner[rcept_no], set()).add(name)
    aliases: dict[str, set[int]] = {}
    for alias, company_id in db.execute(select(CompanyAlias.alias, CompanyAlias.company_id)):
        aliases.setdefault(alias, set()).add(company_id)
    claimed: dict[str, set[int]] = {}   # 원문에서 같은 이름을 쓴 회사들 (분할한 회사는 옛 이름을 나눠 쓴다)
    for company_id, found in names.items():
        for name in found:
            claimed.setdefault(normalize(clean_reported(name)), set()).add(company_id)
    added = []
    for company_id, found in names.items():
        for name in sorted(found):
            key = normalize(clean_reported(name))
            holders = aliases.get(key, set())
            # 다른 회사의 별칭이거나(옛 이름을 다른 회사가 이어 씀: 동아쏘시오홀딩스의 옛 이름 동아제약) 여러 회사가 쓴 이름은 넣지 않는다
            if len(key) < 2 or company_id in holders or holders or len(claimed[key]) > 1:
                continue
            db.add(CompanyAlias(alias=key, company_id=company_id, source="auto"))
            aliases.setdefault(key, set()).add(company_id)
            added.append((db.get(Company, company_id).label, name))
    db.rollback() if dry else db.commit()
    return added


def main(days: float | None = None, dry: bool = False):
    by_code, by_receipt = read(days)
    with session(init_db()) as db:
        added = add(db, by_code, by_receipt, dry)
    for label, name in added if dry else added[:50]:
        print(f"  {label} ← {name}")
    print(f"원문 {sum(map(len, by_code.values())) + len(by_receipt)}개 이름에서 별칭 {len(added)}개를 {'넣을 것' if dry else '넣음'}")


if __name__ == "__main__":
    numbers = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(float(numbers[0]) if numbers else None, dry="--dry" in sys.argv)
