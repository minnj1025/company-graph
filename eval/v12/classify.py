"""평가 12판: 모은 기사 제목을 회사별로 분류한다 (유료: claude-haiku-5-5).

인턴 때의 프롬프트(카테고리 4개, 중요도 0~1, 주가 변동·목표가·지표 기사는 낮게)를 따르고, 방향과 사업 아이템 키워드를 더했다.
결과는 data/cache/v12/labels/{종목코드}.json 에 묶음마다 쌓는다. 끊겨도 이어서 돌릴 수 있다.

실행: python -m eval.v12.classify count                      제목 수와 예상 비용만
      python -m eval.v12.classify run --batches=1            한 묶음만 (모델과 비용 확인용)
      python -m eval.v12.classify run --cap=3.0              전부. 쓴 돈이 cap 달러를 넘으면 멈춘다
"""
import hashlib
import json
import re
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import anthropic

from company_graph.config import CACHE_DIR, secret

MODEL = "claude-haiku-5-5"
PRICE = {"input_tokens": 0.10, "output_tokens": 0.50, "cache_read_input_tokens": 0.01, "cache_creation_input_tokens": 0.125}   # 달러 / 100만 토큰
BATCH = 60
OUT = CACHE_DIR / "v12"
KINDS = ["수주·공급계약", "인수합병·지분", "실적", "신제품·기술", "인허가·임상", "투자·증설", "자금조달·주주환원",
         "소송·제재·사고", "인사·지배구조", "주가·시황", "기타"]

SYSTEM = """당신은 한국 주식시장 뉴스를 분류하는 사람입니다. 한 회사의 이름과 그 회사를 검색해 나온 기사 제목들을 받습니다. 제목마다 아래 칸을 채웁니다.

- about: 이 제목의 주인공이 그 회사인가. 그 회사가 지나가듯 나오거나(업계 순위, 여러 회사 나열), 동명이 다른 대상(야구단, 재단, 인물)이면 false.
- cat: "산업" | "경제" | "정치" | "그외"
  - 산업: 특정 기업·산업군(반도체, 자동차, 바이오 등)의 동향, 수주·계약, 신제품, 연구개발, 투자, 실적 등
  - 경제: 금융, 경제지표, 무역, 재정 정책, 주식시장 관련 소식
  - 정치: 법안, 정부 정책, 외교, 군사, 국제관계
  - 그외: 위에 맞지 않거나 주식시장에 영향이 적은 것(광고성, 행사, 사회공헌, 채용, 스포츠·연예, 인물 동정)
  - 판단이 애매하면 "그외"
- imp: 이 소식이 그 회사 주가에 줄 영향의 크기, 0.00~1.00 (소수 둘째 자리).
  - 높게: 주체(회사)와 대상(상품·서비스·기술)과 행위(수주, 계약, 인수, 승인, 출시, 실적 발표)가 함께 드러난 구체적인 소식
  - 낮게: 맥락이 빠진 소식, 단순 주가·시황 변동(특징주, 급등·급락, 상한가, 신고가, 시가총액 순위), 증권사 목표가 조정·전망, 경제지표, 계획만 있고 핵심이 없는 소식, 추측성 표현이 든 소식, 광고·홍보
- dir: 그 회사에 좋은 소식이면 1, 나쁜 소식이면 -1, 어느 쪽도 아니면 0. 제목에 적힌 사실로만 판단합니다.
- kind: """ + " | ".join(f'"{k}"' for k in KINDS) + """
- kw: 이 소식의 사업 아이템 키워드 1~3개. 회사 이름이 아니라 제품·기술·사업 이름입니다(예: "HBM", "LNG운반선", "ADC", "변압기").
  "기술개발", "신사업", "혁신" 처럼 넓은 말은 쓰지 않습니다. 사업 아이템이 없는 소식이면 빈 목록.

제목은 "번호. 날짜 제목" 꼴로 옵니다. 모든 번호에 하나씩 답합니다."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["i", "about", "cat", "imp", "dir", "kind", "kw"],
    "properties": {"i": {"type": "integer"}, "about": {"type": "boolean"}, "cat": {"type": "string", "enum": ["산업", "경제", "정치", "그외"]},
                   "imp": {"type": "number"}, "dir": {"type": "integer", "enum": [-1, 0, 1]}, "kind": {"type": "string", "enum": KINDS},
                   "kw": {"type": "array", "items": {"type": "string"}}}}}}}


def _clean(title: str) -> str:
    return re.sub(r"\s+-\s+[^-]{1,40}$", "", title or "").strip()   # 끝의 " - 언론사"


def titles() -> dict[str, list[dict]]:
    """회사별로 같은 제목을 합친다. 날짜는 가장 이른 것."""
    universe = {r["stock_code"]: r for r in json.loads((OUT / "universe.json").read_text(encoding="utf-8"))["rows"]}
    out = {}
    for code, row in universe.items():
        seen = {}
        for path in sorted((OUT / "news" / code).glob("*.json")):
            for item in json.loads(path.read_text(encoding="utf-8")):
                title = _clean(item["title"])
                if title and (title not in seen or item["date"] < seen[title]["date"]):
                    seen[title] = {"title": title, "date": item["date"], "source": item.get("source")}
        out[code] = sorted(seen.values(), key=lambda x: (x["date"], x["title"]))
    return out


def batches(by_code: dict[str, list[dict]]):
    names = {r["stock_code"]: r["name"] for r in json.loads((OUT / "universe.json").read_text(encoding="utf-8"))["rows"]}
    for code, items in by_code.items():
        for start in range(0, len(items), BATCH):
            chunk = items[start:start + BATCH]
            key = hashlib.sha1(json.dumps([code, [c["title"] for c in chunk]], ensure_ascii=False).encode()).hexdigest()[:16]
            yield code, names[code], key, chunk


def ask(client, name: str, code: str, chunk: list[dict], usage: Counter, lock) -> list[dict]:
    content = f"회사: {name} ({code})\n\n" + "\n".join(f"{i + 1}. {c['date']} {c['title']}" for i, c in enumerate(chunk))
    response = client.messages.create(
        model=MODEL, max_tokens=8000, system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
    with lock:
        for key in PRICE:
            usage[key] += getattr(response.usage, key, 0) or 0
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"답이 끝까지 오지 않음: {response.stop_reason}")
    if response.model != MODEL and not response.model.startswith(MODEL):
        raise RuntimeError(f"다른 모델이 답함: {response.model}")
    got = {it["i"]: it for it in json.loads(next(b.text for b in response.content if b.type == "text"))["items"]}
    return [{**c, **{k: v for k, v in got.get(i + 1, {}).items() if k != "i"}, "labeled": (i + 1) in got} for i, c in enumerate(chunk)]


def cost(usage: Counter) -> float:
    return sum(usage[k] * p / 1e6 for k, p in PRICE.items())


def run(limit: int | None, cap: float):
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    usage, lock = Counter(), threading.Lock()
    done = defaultdict(dict)
    folder = OUT / "labels"
    folder.mkdir(parents=True, exist_ok=True)
    for path in folder.glob("*.json"):
        done[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    todo = [b for b in batches(titles()) if b[2] not in done[b[0]]]
    if limit:
        todo = todo[:limit]
    print("남은 묶음", len(todo), flush=True)
    stop = threading.Event()

    def one(job):
        code, name, key, chunk = job
        if stop.is_set():
            return
        labeled = ask(client, name, code, chunk, usage, lock)
        with lock:
            done[code][key] = labeled
            (folder / f"{code}.json").write_text(json.dumps(done[code], ensure_ascii=False), encoding="utf-8")
            spent = cost(usage)
            print(f"{code} {name} {len(chunk)}건 · 누적 ${spent:.4f}", flush=True)
            if spent > cap:
                print("상한을 넘어 멈춘다", flush=True)
                stop.set()
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(one, todo))
    print("쓴 돈", f"${cost(usage):.4f}", dict(usage))


def main():
    if sys.argv[1] == "count":
        by_code = titles()
        n = sum(len(v) for v in by_code.values())
        chars = sum(len(c["title"]) + 14 for v in by_code.values() for c in v)
        jobs = sum(1 for _ in batches(by_code))
        # 한국어 1자 ≈ 1토큰으로 넉넉히 잡는다. 답은 제목 하나에 약 45토큰
        est = (chars + jobs * 40) * PRICE["input_tokens"] / 1e6 + jobs * 2000 * PRICE["cache_read_input_tokens"] / 1e6 + n * 45 * PRICE["output_tokens"] / 1e6
        print(f"회사 {len(by_code)}, 제목 {n:,}, 묶음 {jobs:,}, 예상 ${est:.2f}")
    else:
        limit = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--batches=")), None)
        cap = next((float(a.split("=")[1]) for a in sys.argv if a.startswith("--cap=")), 0.05)
        run(limit, cap)


if __name__ == "__main__":
    main()
