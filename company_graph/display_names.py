"""화면과 답에 쓰는 대표 이름(company.display_name)을 만든다.

등기 이름은 "에스케이하이닉스(주)" 처럼 영문 상호를 한글로 풀어 적는다. 사람들이 아는 이름은 "SK하이닉스"다.
등기 이름(company.name)은 그대로 두고 대표 이름을 따로 붙인다. 글자를 통째로 바꾸지 않고, 근거가 있는 것만 바꾼다.

1. 종목명이 있는 회사는 거래소 종목명을 그대로 쓴다. (에스케이하이닉스(주) → SK하이닉스)
2. 종목명이 없는 회사는 등기 이름에서 회사 형태와 주석만 떼고, 아래 둘 중 하나에 해당할 때만 앞머리를 바꾼다.
   - 공정위 명단에서 법인등록번호로 확인한 소속 집단의 이름으로 시작한다. (에스케이 집단의 에스케이온(주) → SK온)
   - 지분·계열 관계로 이어진 상장사와 앞머리가 같고, 그 상장사의 종목명이 그 앞머리를 영문으로 적는다.
     (HLB제약과 이어진 에이치엘비셀(주) → HLB셀)
   이어진 회사가 없으면 "에이치엘비…"를 "HL비…"로 잘못 바꿀 수 있어서 그대로 둔다.

실행: python -m company_graph.display_names          (바뀌는 것만 보여 준다)
      python -m company_graph.display_names --apply  (company 표에 적는다)
"""
import argparse
import json
import re
import unicodedata
from collections import defaultdict

from sqlalchemy import select

from .config import CACHE_DIR
from .db import Company, CompanyAlias, Relation, init_db, session
from .names import clean_reported, normalize

# 공정위가 한글로 적은 집단 이름 가운데, 집단이 스스로 쓰는 표기가 따로 있는 것
GROUP_LABELS = {
    "에스케이": "SK", "엘지": "LG", "엘에스": "LS", "지에스": "GS", "씨제이": "CJ", "케이티": "KT",
    "케이티앤지": "KT&G", "케이씨씨": "KCC", "오씨아이": "OCI", "에이치디씨": "HDC", "에이치엠엠": "HMM",
    "엘엑스": "LX", "엘아이지": "LIG", "엠디엠": "MDM", "에쓰-오일": "S-OIL", "고려에이치씨": "고려HC",
    "아이에스지주": "IS지주", "오케이금융그룹": "OK금융그룹", "한국지엠": "한국GM",
    "QCP그룹(舊 큐로홀딩스)": "QCP그룹", "한국앤컴퍼니그룹(舊 한국타이어)": "한국앤컴퍼니그룹",
}
_GROUP_KEYS = {label: key for key, label in GROUP_LABELS.items()}
# 집단 이름과 소속회사 이름의 앞머리가 다른 집단
_GROUP_PREFIX = {"농협": ("엔에이치", "NH")}
# 규칙으로는 바뀌지만 바꾸면 틀리는 이름 (등기 이름에서 형태를 뗀 꼴로 적는다)
_KEEP = {"엔씨소프트서비스"}

_LETTERS = {"A": ["에이"], "B": ["비"], "C": ["씨"], "D": ["디"], "E": ["이"], "F": ["에프"], "G": ["지"], "H": ["에이치"],
            "I": ["아이"], "J": ["제이"], "K": ["케이"], "L": ["엘"], "M": ["엠"], "N": ["엔"], "O": ["오"], "P": ["피"],
            "Q": ["큐"], "R": ["알"], "S": ["에스", "에쓰"], "T": ["티"], "U": ["유"], "V": ["브이"], "W": ["더블유"],
            "X": ["엑스"], "Y": ["와이"], "Z": ["제트"], "&": ["앤", "엔"]}
_LETTER_NAME = re.compile("|".join(sorted({s for names in _LETTERS.values() for s in names}, key=len, reverse=True)))
_LATIN_HEAD = re.compile(r"^[A-Z&]{2,}")
_SPACED_FORM = re.compile(r"\(\s*(?:주|유|재|사)\s*\)")
# 이름 뒤에 붙은 설명: (구, …), (변경전 상호: …), (지분율 100%), (주2,3), (*2)
_NOTE = re.compile(r"\(+\s*(?:구|舊|전|前|변경|지분율|주\s*\d|[*※]|\d)[^()]*(?:\([^()]*\)[^()]*)*\)*|\(\s*\)|\s주\s*\d+\)")
_EDGE_FORM = re.compile(r"^(?:주식회사|유한회사|유한책임회사)\s*|\s*(?:주식회사|(?<!전문)유한회사|유한책임회사)$")


def group_label(group: str | None) -> str | None:
    """공정위 집단 이름 → 화면에 보일 이름. "에스케이" → "SK" """
    return GROUP_LABELS.get(group, group) if group else group


def group_key(label: str | None) -> str | None:
    """화면의 집단 이름 → 공정위 이름. 어느 쪽으로 받아도 찾을 수 있게 한다."""
    return _GROUP_KEYS.get(label, label) if label else label


def spellings(latin: str) -> list[str]:
    """영문 글자를 한글로 읽은 꼴들. "SK" → ["에스케이", "에쓰케이"] """
    out = [""]
    for letter in latin:
        if letter not in _LETTERS:
            return []
        out = [head + name for head in out for name in _LETTERS[letter]]
    return out


def brand(legal: str, stock_name: str) -> tuple[str, str] | None:
    """등기 이름의 한글 앞머리와 종목명의 영문 앞머리가 같은 글자를 가리키면 그 짝. ("에이치엘비", "HLB")"""
    head = _LATIN_HEAD.match(stock_name or "")
    if not head:
        return None
    key = normalize(legal)
    return next(((spelled, head.group()) for spelled in spellings(head.group()) if key.startswith(spelled)), None)


def tidy(name: str) -> str:
    """등기 이름에서 회사 형태와 주석을 뗀다. "㈜에이치비지주(*2)" → "에이치비지주", "아트마이닝㈜(구,코나메타버스㈜)" → "아트마이닝" """
    text = _SPACED_FORM.sub("(주)", unicodedata.normalize("NFKC", name or ""))
    text = _NOTE.sub(" ", clean_reported(text))
    text = re.sub(r"\(주\)|\(유\)|\(재\)|\(사\)", " ", text).strip()
    text = _EDGE_FORM.sub("", _EDGE_FORM.sub("", text))
    return re.sub(r"\s{2,}", " ", text).strip(" ,") or name


def rename(name: str, pairs: list[tuple[str, str]], confirmed: bool) -> str | None:
    """앞머리를 영문으로 바꾼 이름. 바꿀 근거가 없으면 None.

    confirmed 가 아니면(집단 소속이 아니라 이어진 회사만 보고 바꾸는 경우) 앞머리 뒤가 또 영문 글자 읽기로 이어질 때는 건드리지 않는다.
    "에스지" 뒤에 "이…"가 오면 SG 다음 글자가 E인지 한글 "이"인지 알 수 없다.
    """
    text = tidy(name)
    squeezed = text.replace(" ", "")
    if squeezed in _KEEP:
        return None
    for spelled, latin in sorted(pairs, key=lambda pair: -len(pair[0])):
        if not squeezed.startswith(spelled) or len(squeezed) == len(spelled):
            continue
        if not confirmed and _LETTER_NAME.match(squeezed[len(spelled):]):
            continue
        # 띄어쓰기를 살리면서 앞머리만 바꾼다
        seen, cut = 0, 0
        while seen < len(spelled):
            seen += text[cut] != " "
            cut += 1
        return latin + text[cut:].lstrip()
    return None


def _ftc_groups() -> dict[str, str]:
    """법인등록번호 → 소속 집단. 받아 둔 지정년월을 다 본다 (뒤의 것이 앞의 것을 덮는다)."""
    groups = {}
    for path in sorted((CACHE_DIR / "ftc_affiliates").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            groups[row["jurirno"]] = row["unityGrupNm"]
    return groups


def _stock_names() -> dict[str, str]:
    names = {}
    for path in (CACHE_DIR / "dart_company").glob("*.json"):
        info = json.loads(path.read_text(encoding="utf-8"))
        if (info.get("stock_name") or "").strip():
            names[info["corp_code"]] = info["stock_name"].strip()
    return names


def build(db) -> dict[int, tuple[str, str]]:
    """company_id → (대표 이름, 근거). 근거는 stock(종목명), group(집단), related(이어진 상장사), plain(형태만 뗌)."""
    companies = {c.company_id: c for c in db.scalars(select(Company))}
    stock_names, groups = _stock_names(), _ftc_groups()
    brands = {c.company_id: pair for c in companies.values()
              if (pair := brand(c.name, stock_names.get(c.corp_code or "", "")))}
    near = defaultdict(set)
    for a, b in db.execute(select(Relation.subject_company_id, Relation.object_company_id)
                           .where(Relation.rel_type.in_(("equity", "affiliate")), Relation.retired_at.is_(None))):
        if a and b:
            near[a].add(b)
            near[b].add(a)

    out = {}
    for c in companies.values():
        if c.corp_code in stock_names:
            out[c.company_id] = (stock_names[c.corp_code], "stock")
            continue
        related = [brands[other] for other in near[c.company_id] if other in brands]
        group = c.ftc_group or groups.get(c.jurir_no or "")
        label = group_label(group)
        own = [_GROUP_PREFIX[group]] if group in _GROUP_PREFIX else \
              [(spelled, label) for spelled in spellings(label)] if group and _LATIN_HEAD.fullmatch(label or "") else []
        # 이어진 상장사의 앞머리가 더 길면 그쪽이 먼저다 (SK 집단의 에스케이씨솔믹스 → SKC솔믹스)
        by_related = rename(c.name, related, confirmed=False)
        by_group = rename(c.name, own, confirmed=True)
        if by_related and (not by_group or len(by_related) <= len(by_group)):
            out[c.company_id] = (by_related, "related")
        elif by_group:
            out[c.company_id] = (by_group, "group")
        else:
            out[c.company_id] = (tidy(c.name), "plain")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = init_db()
    with session(engine) as db:
        names = build(db)
        companies = {c.company_id: c for c in db.scalars(select(Company))}
        counts = defaultdict(int)
        for company_id, (shown, why) in sorted(names.items()):
            counts[why] += 1
            if why in ("group", "related"):
                print(f"{why}\t{companies[company_id].name}\t{shown}")
        print("\t".join(f"{why} {n}" for why, n in sorted(counts.items())))
        if not args.apply:
            return
        aliases = set(db.execute(select(CompanyAlias.alias, CompanyAlias.company_id)).all())
        for company_id, (shown, _) in names.items():
            companies[company_id].display_name = shown
            key = normalize(shown)
            if key and (key, company_id) not in aliases:   # 대표 이름으로도 찾을 수 있게
                db.add(CompanyAlias(alias=key, company_id=company_id, source="auto"))
                aliases.add((key, company_id))
        db.commit()
        print("company.display_name 을 적었습니다")


if __name__ == "__main__":
    main()
