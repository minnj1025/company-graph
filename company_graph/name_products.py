"""제품 줄에 표준 이름을 붙인다. Claude API를 부른다(유료).

같은 제품을 회사마다 다르게 적는다("분리막", "LiBS", "2차전지 분리막"). 표에 적힌 이름은 그대로 두고,
여러 회사를 한 제품으로 묶을 표준 이름을 따로 붙인다. 글자가 같은 줄은 한 번만 묻고, 이미 붙인 줄은 다시 묻지 않는다.

실행: python -m company_graph.name_products --estimate     묻지 않고 줄 수와 예상 비용만
      python -m company_graph.name_products --limit 100    100줄만 붙이고 결과를 보여 준다
      python -m company_graph.name_products                남은 줄 전부
      python -m company_graph.name_products --merge        붙인 이름 가운데 같은 제품을 가리키는 것을 하나로 모은다
"""
import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import anthropic
from sqlalchemy import select

from .config import secret
from .db import Company, Product, init_db, session
from .product_names import plain
from .stages import sector

MODEL = "claude-haiku-5-5"
PRICE = {"input_tokens": 0.10, "output_tokens": 0.50}   # 100만 토큰에 달러 (2026-10 기준)
BATCH = 60
WORKERS = 6
MAX_NAMES = 3

SYSTEM = """한국 상장사 사업보고서의 "주요 제품 및 서비스" 표에서 읽은 줄에 표준 제품 이름을 붙입니다.
목적은 서로 다른 회사가 같은 제품을 만들 때 같은 이름으로 묶이게 하는 것입니다.

입력은 줄마다 i(번호), sector(회사 업종), segment(사업부문 칸, 없을 수 있음), name(품목 칸)입니다.
줄마다 names 에 표준 이름을 0~3개 적습니다.

표준 이름을 정하는 법
- 업계에서 그 제품 종류를 부르는 흔한 이름으로 적습니다. 다른 회사도 같은 말로 부를 만큼 일반적이되, 무엇인지 알 수 있을 만큼 구체적이어야 합니다.
  "IH압력밥솥" → "전기밥솥", "2차전지 분리막(LiBS)" → "이차전지 분리막", "색조화장품" → "색조 화장품", "스테인리스강판" → "스테인리스 강판"
- 상표, 모델 이름, 규격은 그것이 속한 제품 종류로 바꿉니다. "갤럭시 S 시리즈" → "스마트폰", "PA66" → "엔지니어링 플라스틱"
- 한 줄에 여러 제품이 나열돼 있으면 대표적인 것을 셋까지 적습니다.
- "부품", "소재", "장비", "솔루션", "서비스"처럼 혼자서는 뜻이 넓은 말은 무엇의 것인지 붙입니다. "자동차 부품", "반도체 장비"
- name 이 "제품", "기타", "상품"처럼 뭉뚱그린 말이면 segment 를 보고 정합니다.
- 한글로 적되 널리 쓰는 영문 약어(MLCC, PCB, OLED, SI)는 그대로 둡니다. 낱말 사이는 띄어 씁니다.

names 를 비워 두는 경우
- 제품이나 서비스가 아닌 줄: 배당, 임대, 지분법, 상표권 수익, 지역 이름, 연결조정, 날짜, 회사 이름
- 상표나 약어뿐이라 sector 와 segment 를 봐도 무슨 제품인지 알 수 없는 줄. 짐작으로 지어내지 않습니다."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["i", "names"], "properties": {
        "i": {"type": "integer"}, "names": {"type": "array", "items": {"type": "string"}}}}}}}

MERGE_SYSTEM = """제품 이름 목록에서 같은 제품을 가리키는 이름들을 찾아 하나로 모읍니다.
이름 옆의 숫자는 그 이름이 붙은 회사 수입니다.

- 표기만 다르거나(띄어쓰기, 한글/영문, 줄임말) 뜻이 같은 이름만 모읍니다. "2차전지 분리막", "이차전지 분리막", "배터리 분리막" → "이차전지 분리막"
- 넓은 이름과 좁은 이름은 모으지 않습니다. "화장품"과 "색조 화장품", "반도체"와 "메모리 반도체"는 따로 둡니다.
- to 에는 모을 이름(목록에 있는 것 가운데 가장 흔하고 알기 쉬운 것), from 에는 그리로 옮길 이름들을 목록에 적힌 그대로 적습니다.
- 모을 것이 없는 이름은 적지 않습니다."""

MERGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["groups"], "properties": {"groups": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["to", "from"], "properties": {
        "to": {"type": "string"}, "from": {"type": "array", "items": {"type": "string"}}}}}}}


def ask(client, system: str, schema: dict, content: str, usage: Counter, effort: str = "low") -> dict:
    with client.messages.stream(model=MODEL, max_tokens=64000, system=system,
                                messages=[{"role": "user", "content": content}],
                                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}}) as stream:
        response = stream.get_final_message()
    for key in PRICE:
        usage[key] += getattr(response.usage, key, 0) or 0
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"답이 끝까지 오지 않았습니다: {response.stop_reason}")
    return json.loads(next(block.text for block in response.content if block.type == "text"))


def cost(usage: Counter) -> float:
    return sum(usage[key] * price / 1e6 for key, price in PRICE.items())


def clean(names: list[str]) -> list[str]:
    kept = []
    for name in names:
        name = " ".join(str(name).split())[:40]
        if name and not plain(name) and name not in kept:
            kept.append(name)
    return kept[:MAX_NAMES]


def pending(db) -> dict[tuple, dict]:
    """아직 표준 이름이 없는 줄을 (사업부문, 이름)으로 묶는다. 업종은 그 글자를 쓴 첫 회사의 것."""
    keys: dict[tuple, dict] = {}
    rows = db.execute(select(Product.segment, Product.name, Company.induty_code)
                      .join(Company, Company.company_id == Product.company_id)
                      .where(Product.named_by.is_(None)).order_by(Product.rcept_no.desc(), Product.row_no))
    for segment, name, induty_code in rows:
        keys.setdefault((segment, name), {"sector": sector(induty_code), "segment": segment, "name": name})
    return keys


def name_all(db, client, limit: int | None, show: bool):
    keys = list(pending(db).items())[:limit]
    batches = [keys[start:start + BATCH] for start in range(0, len(keys), BATCH)]
    usage, done = Counter(), 0

    def answer(batch):
        lines = "\n".join(json.dumps({"i": i, **item}, ensure_ascii=False) for i, (_, item) in enumerate(batch))
        return {a["i"]: clean(a["names"]) for a in ask(client, SYSTEM, SCHEMA, lines, usage)["items"]}

    with ThreadPoolExecutor(WORKERS) as pool:   # 호출 하나가 20초쯤 걸려서 여러 개를 같이 보낸다. DB에는 이 스레드만 쓴다
        for batch, answers in zip(batches, pool.map(answer, batches)):
            for i, ((segment, name), item) in enumerate(batch):
                if i not in answers:   # 모델이 빠뜨린 줄은 다음에 다시 묻는다
                    continue
                for row in db.scalars(select(Product).where(Product.named_by.is_(None), Product.name == name,
                                                            Product.segment.is_(None) if segment is None else Product.segment == segment)):
                    row.std_names, row.named_by = answers[i], MODEL
                if show:
                    print(f"{item['sector']} | {segment or '-'} | {name}  →  {', '.join(answers[i]) or '(없음)'}")
            db.commit()
            done += len(batch)
            print(f"  {done}/{len(keys)}줄, 지금까지 {cost(usage):.3f}달러", flush=True)
    print(f"토큰 {dict(usage)} | {cost(usage):.3f}달러")


def spelling(name: str) -> str:
    """띄어쓰기와 숫자 표기만 다른 이름을 같은 열쇠로 만든다. "통신장비" = "통신 장비", "2차전지" = "이차전지" """
    return name.replace(" ", "").replace("2차전지", "이차전지").lower()


def merge(db, client):
    """같은 제품을 가리키는 표준 이름을 하나로 모은다. 표기만 다른 것은 규칙으로, 뜻이 같은 것은 모델에게 물어서."""
    rows = db.scalars(select(Product).where(Product.std_names.is_not(None))).all()

    def count() -> dict[str, set]:
        companies = defaultdict(set)
        for row in rows:
            for name in row.std_names:
                companies[name].add(row.company_id)
        return companies

    def move(moves: dict[str, str]):
        for row in rows:
            merged = list(dict.fromkeys(moves.get(name, name) for name in row.std_names))
            if merged != row.std_names:
                row.std_names = merged
        db.commit()

    companies = count()
    total = len(companies)
    # 1. 표기만 다른 이름은 가장 많은 회사가 쓰는 표기로
    spellings = defaultdict(list)
    for name in companies:
        spellings[spelling(name)].append(name)
    by_rule = {}
    for names in spellings.values():
        best = max(names, key=lambda name: (len(companies[name]), " " in name, name))
        by_rule.update({name: best for name in names if name != best})
    move(by_rule)
    # 2. 뜻이 같은 이름은 모델에게 묻는다. 목록에 없는 이름으로는 옮기지 않는다
    companies = count()
    usage = Counter()
    listing = "\n".join(f"{name}\t{len(ids)}" for name, ids in sorted(companies.items()))
    by_model = {}
    for group in ask(client, MERGE_SYSTEM, MERGE_SCHEMA, listing, usage, effort="medium")["groups"]:
        for old in group["from"]:
            if old in companies and group["to"] in companies and old != group["to"] and group["to"] not in by_model:
                by_model[old] = group["to"]
                print(f"{old} ({len(companies[old])}) → {group['to']} ({len(companies[group['to']])})")
    move(by_model)
    print(f"이름 {total:,}개 가운데 표기가 달라 옮긴 것 {len(by_rule):,}개, 뜻이 같아 옮긴 것 {len(by_model):,}개 "
          f"| 토큰 {dict(usage)} | {cost(usage):.3f}달러")


def main():
    parser = argparse.ArgumentParser(description="제품 줄에 표준 이름을 붙인다 (Claude API 사용, 유료)")
    parser.add_argument("--estimate", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    with session(init_db()) as db:
        if args.estimate:
            keys = pending(db)
            chars = sum(len(json.dumps(item, ensure_ascii=False)) + 10 for item in keys.values())
            calls = -(-len(keys) // BATCH)
            # 한글은 글자 하나가 토큰 하나쯤이다. 줄마다 답은 40토큰쯤으로 넉넉히 잡는다
            guess = Counter(input_tokens=chars + calls * len(SYSTEM), output_tokens=len(keys) * 40)
            print(f"물을 줄 {len(keys):,}개, 호출 {calls}번, 예상 토큰 {dict(guess)}, 예상 비용 {cost(guess):.2f}달러 ({MODEL})")
            return
        client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
        if args.merge:
            merge(db, client)
        else:
            name_all(db, client, args.limit, show=args.limit is not None)


if __name__ == "__main__":
    main()
