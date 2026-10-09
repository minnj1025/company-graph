"""규칙으로 읽지 못한 회사의 제품 표를 글을 읽고 옮기는 일의 준비와 반영.

product_parser 는 "더해서 100이 되는 묶음"을 찾는 규칙이라, 표 모양이 특이하거나(매출·영업이익·자산이 한 표에 섞임, 줄이 끊김)
비중 없이 글로만 적은 회사는 읽지 못한다. 그런 회사는 "주요 제품" 절의 글을 그대로 내보내고, 읽는 쪽(사람이든 모델이든)이
같은 안내문을 보고 줄로 옮긴다. 옮긴 줄은 read_by = "model" 로 표시해 규칙으로 읽은 것과 가린다.
보고서가 비중을 밝히지 않은 제품은 비중을 비워 둔다(지어내지 않는다).

실행: python -m company_graph.product_reading export   data/product_reading/ 에 안내문과 일감 묶음을 쓴다
      python -m company_graph.product_reading check    결과 파일에서 빠진 회사, 읽을 수 없는 줄을 찾는다
      python -m company_graph.product_reading apply    결과를 DB에 반영한다
"""
import json
import re
import sys
from collections import Counter, defaultdict

from sqlalchemy import delete, select

from .config import DATA_DIR
from .db import BusinessSection, Company, Product, init_db, session
from .product_names import candidate

WORK = DATA_DIR / "product_reading"
CHARS_PER_BATCH = 110_000
MAIN_CHARS, SALES_CHARS = 7000, 3000
LISTED = ("Y", "K")
SHELL = re.compile(r"스팩|기업인수목적")   # 사업이 없는 회사

GUIDE = """# 사업보고서에서 "무엇을 얼마나 파는가"를 줄로 옮기기

한국 상장사의 사업보고서(또는 반기보고서) "II. 사업의 내용"에서, 그 회사가 **무엇을 팔고 그것이 매출의 몇 %인지**를 줄로 옮깁니다.
규칙으로는 읽지 못한 회사들입니다. 표 모양이 특이하거나, 비중 없이 글로만 적었거나, 금융회사라 제품 표가 없습니다.
이 결과는 실제 서비스의 조회와 그래프에 그대로 쓰입니다. **보고서에 적힌 것만 옮깁니다. 지어내지 않습니다.**

## 일감의 모양

회사마다 머리 줄 하나와 보고서의 글이 옵니다. 표는 칸을 ` | ` 로 나눠 한 줄에 한 행씩 적혀 있습니다.

    ## 12345 | 호텔신라 | 20260814001955
    [2. 주요 제품 및 서비스]
    (글과 표)
    [4. 매출 및 수주상황]
    (글과 표)

머리 줄의 첫 숫자가 회사 번호입니다.

## 답의 모양

회사마다 줄을 적습니다. 칸은 탭으로 나눕니다.

    회사번호<TAB>사업부문<TAB>품목<TAB>비중

    12345	TR	면세점	82.3
    12345	호텔&레저	호텔, 레저 시설	19.3
    23456	-	로수젯(고지혈증 치료제)	?
    34567	-

- **사업부문**: 표에 부문 칸이 따로 있으면 그 값. 없으면 `-`
- **품목**: 무엇을 파는지. 표의 품목 칸을 그대로 옮기되, 칸에 "제품", "매출"처럼 뜻 없는 말만 있으면 부문 이름이나 주요 제품 설명을 적습니다
- **비중**: 그 줄이 매출에서 차지하는 비중(%). 숫자만 적습니다. 알 수 없으면 `?`
- 팔 것이 없는 회사(사업을 하지 않는 회사, 글에 아무 단서가 없는 회사)는 `회사번호<TAB>-` 한 줄만 적습니다

모든 회사가 한 번 이상 나와야 합니다.

## 비중을 정하는 법

1. **표에 비중(%)이 적혀 있으면 그대로** 옮깁니다. 기간이 여럿이면 가장 최근 기간(당기, 당반기)의 값.
2. **비중은 없고 매출액만 있으면**, 전체 매출(합계 줄)이 표에 있을 때만 `매출액 ÷ 합계 × 100` 을 소수 첫째 자리까지 계산해 적습니다.
3. **전체를 알 수 없으면 `?`**. 매출이 큰 제품 몇 개만 적은 표, 글로만 나열한 제품이 그렇습니다. 어림하거나 고르게 나누지 않습니다.
4. 한 표에 **매출, 영업이익, 자산이 섞여 있으면 매출 줄만** 봅니다.
5. **여러 회사의 표가 이어져 있으면**(모회사와 종속회사를 각각 100%로 적은 표) 연결 기준 표가 있으면 그것을, 없으면 이 회사(머리 줄의 회사) 것만 옮깁니다.
6. **합계, 소계, 내부거래 제거, 연결조정 줄은 옮기지 않습니다.** 그래서 옮긴 줄의 합이 100을 조금 넘거나 모자라도 됩니다.
7. 같은 품목이 제품과 상품으로 나뉘어 두 번 나오면 두 줄 그대로 옮깁니다.

## 금융회사

은행, 증권, 보험, 캐피탈, 지주회사는 제품 표가 없습니다. "영업의 현황" 같은 절에서 **영업 부문이나 수익의 구성**을 옮깁니다.

    45678	-	이자수익	61.2
    45678	-	수수료수익	18.4

구성 비율이 적혀 있지 않으면 부문 이름만 적고 비중은 `?` 로 둡니다. 순이자마진, 건전성 비율 같은 지표는 품목이 아닙니다.

## 하지 않는 것

- 보고서에 없는 제품을 적지 않습니다. 회사 이름을 보고 아는 것을 보태지 않습니다.
- 가격 변동 표, 생산 능력 표, 원재료 표, 수주 표, 매출처(거래처) 표는 제품 표가 아닙니다.
- 한 회사에 줄을 20개 넘게 적지 않습니다. 넘으면 비중이 큰 것부터 20줄.
"""


def unread(db) -> list[tuple[Company, str, list[BusinessSection]]]:
    """상장사 가운데 사업 내용은 있는데 제품 줄이 없는 회사와, 그 가장 나중 보고서의 절들."""
    have = set(db.scalars(select(Product.company_id).distinct()))
    sections: dict[int, list[BusinessSection]] = defaultdict(list)
    for section in db.scalars(select(BusinessSection).order_by(BusinessSection.rcept_no.desc(), BusinessSection.section_no)):
        sections[section.company_id].append(section)
    out = []
    for company in db.scalars(select(Company).where(Company.corp_cls.in_(LISTED), Company.company_id.in_(list(sections)))):
        if company.company_id in have or SHELL.search(company.name + (company.display_name or "")):
            continue
        latest = sections[company.company_id][0].rcept_no
        out.append((company, latest, [s for s in sections[company.company_id] if s.rcept_no == latest]))
    return sorted(out, key=lambda item: item[0].company_id)


def _text(sections: list[BusinessSection]) -> str:
    """읽을 글: "주요 제품" 절과 "매출" 절. 그 절이 없는 회사(금융업)는 앞의 두 절."""
    main = [s for s in sections if "주요 제품" in s.title]
    sales = [s for s in sections if "매출" in s.title and "주요 제품" not in s.title]
    picked = [(s, MAIN_CHARS) for s in main[:1]] + [(s, SALES_CHARS) for s in sales[:1]] if main else [(s, MAIN_CHARS) for s in sections[:2]]
    return "\n".join(f"[{s.title}]\n{s.text[:limit]}" for s, limit in picked)


def export():
    WORK.mkdir(parents=True, exist_ok=True)
    with session(init_db()) as db:
        items = [(company.company_id, company.label, rcept_no, _text(sections)) for company, rcept_no, sections in unread(db)]
    (WORK / "GUIDE.md").write_text(GUIDE, encoding="utf-8")
    for old in WORK.glob("batch_*.txt"):
        old.unlink()
    batches, current, size = [], [], 0
    for company_id, name, rcept_no, text in items:
        block = f"## {company_id} | {name} | {rcept_no}\n{text}"
        if current and size + len(block) > CHARS_PER_BATCH:
            batches.append(current)
            current, size = [], 0
        current.append(block)
        size += len(block)
    batches.append(current)
    for number, batch in enumerate(batches, 1):
        (WORK / f"batch_{number:02d}.txt").write_text("\n\n".join(batch) + "\n", encoding="utf-8")
    (WORK / "index.json").write_text(json.dumps({str(company_id): rcept_no for company_id, _, rcept_no, _ in items}), encoding="utf-8")
    print(f"회사 {len(items):,}곳, 묶음 {len(batches)}개 → {WORK}")


def read_results() -> tuple[dict[int, list[tuple]], list[str]]:
    """{회사 번호: [(사업부문, 품목, 비중 또는 None), …]} 와 문제 목록. 팔 것이 없다고 한 회사는 빈 목록."""
    index = json.loads((WORK / "index.json").read_text(encoding="utf-8"))
    results: dict[int, list[tuple]] = {}
    problems = []
    for path in sorted(WORK.glob("out_*.tsv")):
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = [part.strip() for part in line.split("\t")]
            if not line.strip():
                continue
            if not parts[0].isdigit() or parts[0] not in index:
                problems.append(f"{path.name}: 모르는 회사 번호 {line[:50]!r}")
                continue
            rows = results.setdefault(int(parts[0]), [])
            if len(parts) == 2 and parts[1] == "-":
                continue
            if len(parts) != 4 or not parts[2]:
                problems.append(f"{path.name}: 읽을 수 없는 줄 {line[:60]!r}")
                continue
            share = None
            if parts[3] not in ("?", ""):
                try:
                    share = float(parts[3].replace("%", "").replace(",", ""))
                except ValueError:
                    problems.append(f"{path.name}: 비중을 읽을 수 없음 {line[:60]!r}")
                    continue
                if share <= 0:      # 당기 매출이 없는 품목, 반품으로 음수가 된 줄: 담지 않는다
                    continue
                if share > 200:
                    problems.append(f"{path.name}: 비중이 너무 큼 {line[:60]!r}")
                    continue
            rows.append((None if parts[1] in ("-", "") else parts[1][:100], parts[2][:100], share))
    # 부문 매출을 내부거래를 빼기 전 금액으로 적고 합계는 뺀 뒤 금액으로 적은 표는, 줄을 더하면 100을 크게 넘는다(131.5 등).
    # 그런 회사는 줄들의 합을 100으로 놓고 다시 나눈다 (내부거래를 빼기 전 매출에서의 몫이 된다).
    for company_id, rows in results.items():
        total = sum(share for _, _, share in rows if share is not None)
        if total > 110 and all(share is not None for _, _, share in rows):
            results[company_id] = [(segment, name, round(share / total * 100, 1)) for segment, name, share in rows]
        elif total > 110:
            problems.append(f"회사 {company_id}: 비중의 합이 {total:.1f}")
    missing = [company_id for company_id in index if int(company_id) not in results]
    if missing:
        problems.append(f"빠진 회사 {len(missing):,}곳 (예: {missing[:8]})")
    return results, problems


def check():
    results, problems = read_results()
    rows = [row for found in results.values() for row in found]
    print(f"결과가 있는 회사 {len(results):,}곳 | 팔 것이 없다고 한 회사 {sum(1 for r in results.values() if not r):,} | "
          f"줄 {len(rows):,}개 (비중 없음 {sum(1 for r in rows if r[2] is None):,})")
    for problem in problems[:60]:
        print("  문제:", problem)
    print(f"문제 {len(problems):,}건")


def apply():
    index = json.loads((WORK / "index.json").read_text(encoding="utf-8"))
    results, problems = read_results()
    if any("비중의 합" in p for p in problems):
        sys.exit("비중의 합이 맞지 않는 회사가 있어 반영하지 않습니다. check 로 확인하세요")
    stats = Counter()
    with session(init_db()) as db:
        reports = {s.rcept_no: s for s in db.scalars(select(BusinessSection).where(BusinessSection.rcept_no.in_(list(index.values()))))}
        for company_id, rows in results.items():
            rcept_no = index[str(company_id)]
            if not rows:
                stats["팔 것이 없다고 한 회사"] += 1
                continue
            # 규칙으로 읽은 줄이 있는 보고서는 건드리지 않는다
            if db.scalar(select(Product.product_id).where(Product.rcept_no == rcept_no, Product.read_by.is_distinct_from("model")).limit(1)):
                stats["규칙으로 이미 읽은 회사"] += 1
                continue
            db.execute(delete(Product).where(Product.rcept_no == rcept_no))
            first = reports[rcept_no]
            for row_no, (segment, name, share) in enumerate(rows, 1):
                plain = candidate(segment, name) is None
                db.add(Product(company_id=company_id, rcept_no=rcept_no, bsns_year=first.bsns_year, disclosed_date=first.disclosed_date,
                               row_no=row_no, segment=segment, name=name, share_pct=share, read_by="model",
                               std_names=[] if plain else None, named_by="rule" if plain else None))
                stats["담은 줄"] += 1
                stats["비중이 없는 줄"] += share is None
            stats["담은 회사"] += 1
        db.commit()
    for key, value in sorted(stats.items()):
        print(f"{key}: {value:,}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    {"export": export, "check": check, "apply": apply}.get(command, lambda: sys.exit(__doc__))()
