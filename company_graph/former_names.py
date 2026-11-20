"""회사의 옛 이름을 별칭으로 넣는다. 이름을 바꾼 회사를 옛 이름으로 물어도 찾게 한다.

DART의 회사 목록(corpCode, 기업개황)과 공시 목록은 지금 이름만 준다. 공시 원문에는 공시한 때의 이름이 남아 있으므로,
이미 받아 둔 원문(data/cache/dart_document)에서 읽는다. DART를 부르지 않는다.
- 일반 공시: <COMPANY-NAME AREGCIK="고유번호">이름  → 고유번호로 회사를 정한다
- 거래소 공시(접수번호 9번째 자리 8): <title>이름/보고서명  → 접수번호로 document 표에서 회사를 정한다
이름이 이미 다른 회사의 별칭이거나 원문에서 여러 회사가 쓴 이름이면 넣지 않는다(옛 이름을 다른 회사가 이어 쓰거나 나눠 쓴 경우).

2026-11 평가 16판 P40: 'LIG넥스원'으로 찾지 못했다(원장 이름 LIG디펜스앤에어로스페이스, 별칭에 옛 이름이 없었다).

실행: python -m company_graph.former_names [--dry]   (--dry 면 넣지 않고 넣을 것만 보인다)
"""
import json
import re
import sys

from sqlalchemy import select

from .config import CACHE_DIR
from .db import Company, CompanyAlias, Document, init_db, session
from .names import clean_reported, normalize

DOCUMENTS = CACHE_DIR / "dart_document"
SEEN = CACHE_DIR / "former_names_seen.json"   # 원문 파일마다 읽은 (고유번호, 이름)
_HEAD = re.compile(rb'<COMPANY-NAME AREGCIK="(\d{8})">([^<]+)')
_TITLE = re.compile(rb"<title>([^<]+?)/")


def read() -> tuple[dict[str, set[str]], dict[str, str]]:
    """(고유번호 → 원문에 적힌 이름들, 거래소 공시 접수번호 → 제목의 이름)
    원문을 여는 데 오래 걸려(2만 6천 개에 10분 넘게) 파일마다 읽은 것을 SEEN 에 남기고, 다음에는 새로 받은 원문만 연다"""
    try:
        seen = json.loads(SEEN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    for path in DOCUMENTS.glob("*.xml"):
        if path.stem in seen:
            continue
        with open(path, "rb") as f:
            head = f.read(6000)
        found = _HEAD.search(head)
        if found:
            seen[path.stem] = [found.group(1).decode(), found.group(2).decode("utf-8", "replace").strip()]
        elif (found := _TITLE.search(head)) and path.stem[8:9] == "8":
            seen[path.stem] = [None, found.group(1).decode("utf-8", "replace").strip()]
        else:
            seen[path.stem] = None
    SEEN.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    by_code: dict[str, set[str]] = {}
    by_receipt: dict[str, str] = {}
    for stem, item in seen.items():
        if item and item[0]:
            by_code.setdefault(item[0], set()).add(item[1])
        elif item:
            by_receipt[stem] = item[1]
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
    labels = {i: shown or name for i, shown, name in db.execute(
        select(Company.company_id, Company.display_name, Company.name).where(Company.company_id.in_(list(names))))}
    added, rows = [], []
    for company_id, found in names.items():
        for name in sorted(found):
            key = normalize(clean_reported(name))
            holders = aliases.get(key, set())
            # 다른 회사의 별칭이거나(옛 이름을 다른 회사가 이어 씀: 동아쏘시오홀딩스의 옛 이름 동아제약) 여러 회사가 쓴 이름은 넣지 않는다
            if len(key) < 2 or holders or len(claimed[key]) > 1:
                continue
            rows.append(CompanyAlias(alias=key, company_id=company_id, source="auto"))
            aliases.setdefault(key, set()).add(company_id)
            added.append((labels.get(company_id, str(company_id)), name))
    if not dry:
        db.add_all(rows)
        db.commit()
    return added


def main(dry: bool = False):
    by_code, by_receipt = read()
    with session(init_db()) as db:
        added = add(db, by_code, by_receipt, dry)
    for label, name in added if dry else added[:50]:
        print(f"  {label} ← {name}")
    print(f"원문 {sum(map(len, by_code.values())) + len(by_receipt)}개 이름에서 별칭 {len(added)}개를 {'넣을 것' if dry else '넣음'}")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
