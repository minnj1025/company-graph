"""제품 줄에 표준 이름과 제품군을 붙이는 일의 준비와 반영.

이름 붙이기는 회사 하나의 표 전체와 사업 개요를 같이 보고 해야 맞는다. 상표("토망고")나 뭉뚱그린 칸("제품")만 봐서는
무엇인지 알 수 없기 때문이다. 그래서 일감을 회사 단위로 묶어 글 파일로 내보내고, 붙인 결과를 받아 DB에 반영한다.
붙이는 주체(사람이든 모델이든)는 같은 안내문(GUIDE)과 같은 제품군 목록(product_families)을 본다.

실행: python -m company_graph.product_naming export     data/product_naming/ 에 안내문과 일감 묶음을 쓴다
      python -m company_graph.product_naming check      결과 파일에서 빠진 줄, 목록에 없는 제품군을 찾는다
      python -m company_graph.product_naming apply 이름  결과를 DB에 반영한다 (이름은 named_by 에 적을 값)
"""
import json
import re
import sys
from collections import Counter, defaultdict

from sqlalchemy import func, select

from .config import DATA_DIR
from .db import BusinessSection, Company, Product, init_db, session
from .product_families import FAMILIES, FAMILY_FIELD
from .stages import sector

WORK = DATA_DIR / "product_naming"
# 이름을 붙일 때 맞는 제품군이 없어 "기타"로 둔 제품을, 나중에 제품군 목록을 고치면서 옮겨 준 것. 제품 이름 → 제품군
REFILE = {
    **dict.fromkeys(("전시회 개최", "전시장 운영", "전시 부스 디자인·설치"), "전시·컨벤션"),
    **dict.fromkeys(("소화기", "자동식 소화기", "소화 기구"), "소방 제품"),
    **dict.fromkeys(("전동공구", "톱", "줄자", "커터칼"), "전동공구·수공구"),
    **dict.fromkeys(("광물 자원", "광산물", "원유·가스 개발"), "자원 개발"),
    **dict.fromkeys(("우드칩", "바이오매스 연료"), "목재·목질 보드"),
    **dict.fromkeys(("점자 정보 단말기", "전자 독서 확대기", "음성 독서기", "시각장애인 보조공학 기기"), "치료·수술 기기"),
    **dict.fromkeys(("건설장비 렌탈", "사무기기 렌탈", "계측기 렌탈", "건설 장비 임대"), "장비 렌탈"),
    **dict.fromkeys(("미술품 판매", "미술품 경매", "미술품 중개"), "미술품 경매"),
    **dict.fromkeys(("태양전지 제조 장비", "태양광 제조 장비"), "태양광"),
    **dict.fromkeys(("전자가속기", "전자가속기 유지보수", "전자선 조사 서비스", "가속기 부품"), "계측·검사·레이저 장비"),
    **dict.fromkeys(("철강 부원료",), "철강"),
    **dict.fromkeys(("철강 제품 포장", "무인 주차 운영", "공공자전거 무인대여 시스템"), "사업지원 서비스"),
    "제대혈 보관": "세포·제대혈 보관", "탄소배출권": "전력·가스·열 공급", "스마트홈 시스템": "영상감시·출입보안 장비",
    "카메라 모듈 장비": "전자부품 제조 장비", "초전도 선재 제조 장비": "일반 산업기계", "마이크로그리드 솔루션": "에너지저장장치",
    "투자 정보 서비스": "금융 서비스", "블록체인 플랫폼": "인터넷 플랫폼·이커머스", "유해동물 기피제": "생활용품", "우모": "섬유 소재",
    "새싹 재배기": "생활 가전", "소독기": "생활 가전", "피아노": "스포츠·레저 용품", "응원봉": "음악·공연·매니지먼트",
    "분리막 모듈": "환경·폐기물",
}
# 처음에는 "기타 전자부품"에 들어 있던 모터류. 제품군을 따로 세웠다
MOTORS = ("모터", "소형 모터", "BLDC 모터", "스테핑 모터", "DC 모터", "AC 모터", "기어드 모터", "모터 코어", "모터 컨트롤러", "권선 코일")
ROWS_PER_BATCH = 380
MAX_PRODUCTS = 8

GUIDE = """# 제품 줄에 표준 이름과 제품군 붙이기

한국 상장사가 사업보고서의 "주요 제품 및 서비스" 표에 적은 줄을 읽고, 줄마다 **무엇을 파는 것인지**를 표준 이름으로 적습니다.
목적은 서로 다른 회사가 같은 것을 팔 때 같은 이름으로 묶이게 하는 것입니다. 이 결과는 실제 서비스의 조회와 그래프에 그대로 쓰입니다.

## 일감의 모양

회사마다 머리 줄 하나와 그 회사의 표 줄들이 옵니다.

    ## (주)쿠쿠홀딩스 | 업종: 금융 | 개요: 당사는 지주회사로서 ... 전기밥솥 등 주방가전을 ...
    12031 | 주요종속회사(가전사업 :쿠쿠전자 등) | IH압력밥솥 | 47.22
    12032 | 당사(쿠쿠홀딩스) | 임대료 | 0.84

표 줄은 `번호 | 사업부문 칸 | 품목 칸 | 매출 비중(%)` 입니다. 사업부문 칸이 없으면 `-` 입니다.
회사마다 칸을 다르게 씁니다. 품목 칸에 "제품", "기타"만 적고 정작 무엇인지는 사업부문 칸에 적은 회사가 많습니다.
**한 줄만 보지 말고 그 회사의 다른 줄과 개요를 같이 보고** 무엇인지 판단합니다.

## 답의 모양

표 줄마다 한 줄씩, 번호 순서대로 씁니다. 모든 번호가 정확히 한 번씩 나와야 합니다.

    12031<TAB>IH 전기밥솥=생활 가전
    12032<TAB>-
    12040<TAB>D램=메모리 반도체; 낸드 플래시=메모리 반도체; 모바일 AP=시스템 반도체
    12051<TAB>자동차 부품=기타 자동차 부품 ?

- `제품 이름=제품군` 을 `; ` 로 이어 적습니다. 제품이라 할 것이 없으면 `-` 만 적습니다.
- 줄 끝의 ` ?` 는 "짐작이 섞였다"는 표시입니다. 표와 개요만으로 확신할 수 없을 때 붙입니다.

## 제품 이름을 정하는 법

1. **그 제품 종류를 업계에서 부르는 흔한 이름**으로 적습니다. 다른 회사도 같은 말로 부를 만큼 일반적이되, 무엇인지 알 수 있을 만큼 구체적이어야 합니다.
   "IH압력밥솥" → IH 전기밥솥 / "LiBS" → 이차전지 분리막 / "스테인리스강판" → 스테인리스 강판
2. **상표, 모델 이름, 규격은 그것이 속한 제품 종류로** 바꿉니다. 무엇인지 알면 바꾸고("나보타" → 보툴리눔 톡신, "갤럭시" → 스마트폰),
   개요와 다른 줄을 봐도 모르겠으면 사업부문 칸으로 정하고 ` ?` 를 붙이거나, 그것도 안 되면 `-` 로 둡니다. 지어내지 않습니다.
3. **한 줄에 여러 제품이 나열돼 있으면 나열된 것을 모두** 적습니다(최대 8개, 앞에서부터). "등", "외"는 버립니다.
   "TV, 모니터, 냉장고, 세탁기, 에어컨, 스마트폰, 네트워크시스템, PC 등" → 여덟 가지 모두
4. **품목 칸이 뭉뚱그린 말("제품", "상품", "기타", "제품 외")이면 사업부문 칸으로** 정합니다.
   "콘덴서 외 | 제 품" → 콘덴서 / "철강부문 | 제품 등" → 철강 제품(사업부문만으로는 더 좁힐 수 없을 때)
   다만 같은 사업부문에 구체적인 줄이 따로 있고 이 줄은 "기타"인 경우에는 `-` 로 둡니다(그 부문의 나머지라는 뜻이라 새 정보가 없습니다).
5. **넓은 말을 혼자 쓰지 않습니다.** "부품", "소재", "장비", "솔루션", "서비스", "용역"은 무엇의 것인지 붙입니다. "자동차 부품", "반도체 장비"
6. **그 회사가 실제로 파는 것**을 적습니다. 설비 회사가 "2차전지"라고 적었으면 이차전지가 아니라 이차전지 장비입니다. 개요를 보고 가립니다.
   "조선 LNG"를 적은 기자재 회사는 LNG선이 아니라 선박용 보냉재나 선박 부품입니다.
7. 표기: 한글로 적고 낱말 사이는 띄어 씁니다("반도체 장비", "이차전지 분리막"). "2차전지"가 아니라 "이차전지".
   널리 쓰는 영문 약어는 그대로 둡니다(D램 제외: MLCC, OLED, LED, PCB 대신 "인쇄회로기판", SI 대신 "시스템 통합 서비스", ESS 대신 "에너지저장장치").
   아래 제품군 목록의 예에 같은 것이 있으면 **그 표기를 그대로** 씁니다.
8. 의약품은 효능군이나 제형이 아니라 **무엇을 치료하는지**로 적되("고혈압 치료제", "항암제"), 상표만 있고 무엇인지 모르면 "전문의약품"이나 "일반의약품"으로 둡니다.

## `-` 로 두는 줄

- 제품이나 서비스가 아닌 것: 배당, 지분법 이익, 상표권·브랜드 사용료, 임대료(부동산 임대가 본업이 아닌 회사), 이자, 연결조정, 내부거래 제거
- 지역이나 판매 경로로 나눈 줄: 국내, 수출, 미국, 대리점 판매.
  **예외**: 회사의 표 전체가 지역으로만 나뉘어 있고 사업부문 칸이나 개요가 무엇을 파는지 말해 주면, 그 줄들에 그 제품을 적습니다
  (예: 사업부문 "자동차 부품" 아래 한국·유럽·미국 줄만 있는 내외장 부품 회사 → 줄마다 `자동차 내장재=자동차 내외장 부품`). 이렇게 해야 그 회사가 그래프에서 빠지지 않습니다.
- 날짜, 회사 이름, 합계처럼 표를 잘못 읽은 줄
- 같은 부문의 구체적인 줄 옆에 있는 "기타"
- 매출 비중이 0 이하인 줄

## 제품군을 고르는 법

- 제품마다 아래 목록에서 **하나**를 고릅니다. 목록에 있는 이름을 글자 그대로 씁니다.
- 기준은 "같은 제품군의 회사끼리 경쟁하거나 대체할 수 있는가"입니다. 반도체를 만드는 회사와 반도체 장비를 만드는 회사는 다른 제품군입니다.
- 무엇을 만드는 데 쓰이는지가 아니라 **그 자체가 무엇인지**로 고릅니다. 자동차용 인쇄회로기판은 "인쇄회로기판"입니다.
  다만 자동차에만 쓰이는 부품은 자동차 부품의 제품군에 넣습니다.
- 맞는 제품군이 정말 없으면 `기타` 라고 적습니다. 억지로 끼워 넣지 않습니다.

## 제품군 목록 (분야 > 제품군: 그 제품군에 드는 제품 이름의 예)

"""


def guide() -> str:
    lines, field = [], None
    for name, family, examples in FAMILIES:
        if name != field:
            field = name
            lines.append(f"\n### {field}")
        lines.append(f"- **{family}**: {examples}")
    return GUIDE + "\n".join(lines) + "\n"


def collect(db) -> list[dict]:
    """회사마다 보고서들에 나온 줄을 (사업부문, 품목)으로 모은다. 비중은 가장 나중 보고서의 것."""
    overview = {}
    latest = (select(BusinessSection.company_id, func.max(BusinessSection.rcept_no).label("rcept_no"))
              .group_by(BusinessSection.company_id).subquery())
    for row in db.scalars(select(BusinessSection).join(latest, BusinessSection.rcept_no == latest.c.rcept_no)
                          .where(BusinessSection.section_no.in_((0, 1))).order_by(BusinessSection.section_no)):
        text = re.sub(r"\s+", " ", " ".join(line for line in row.text.split("\n") if " | " not in line))
        overview[row.company_id] = text[:260]
    tables: dict[int, dict[tuple, float]] = defaultdict(dict)
    for row in db.scalars(select(Product).order_by(Product.rcept_no.desc(), Product.row_no)):
        tables[row.company_id].setdefault((row.segment, row.name), float(row.share_pct))
    companies = {c.company_id: c for c in db.scalars(select(Company).where(Company.company_id.in_(list(tables))))}
    out = []
    for company_id in sorted(tables):
        company = companies[company_id]
        out.append({"company_id": company_id, "name": company.name, "sector": sector(company.induty_code),
                    "overview": overview.get(company_id, ""),
                    "rows": [{"segment": segment, "name": name, "share": share} for (segment, name), share in tables[company_id].items()]})
    return out


def export():
    WORK.mkdir(parents=True, exist_ok=True)
    with session(init_db()) as db:
        companies = collect(db)
    (WORK / "GUIDE.md").write_text(guide(), encoding="utf-8")
    index, batches, current, count = [], [], [], 0
    for company in companies:
        if count and count + len(company["rows"]) > ROWS_PER_BATCH:
            batches.append(current)
            current, count = [], 0
        lines = [f"## {company['name']} | 업종: {company['sector']} | 개요: {company['overview'] or '(없음)'}"]
        for row in company["rows"]:
            index.append([company["company_id"], row["segment"], row["name"]])
            lines.append(f"{len(index) - 1} | {row['segment'] or '-'} | {row['name']} | {row['share']:g}")
        current.append("\n".join(lines))
        count += len(company["rows"])
    batches.append(current)
    for number, batch in enumerate(batches, 1):
        (WORK / f"batch_{number:02d}.txt").write_text("\n\n".join(batch) + "\n", encoding="utf-8")
    (WORK / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"회사 {len(companies):,}곳, 줄 {len(index):,}개, 묶음 {len(batches)}개 → {WORK}")


def read_results() -> tuple[dict[int, tuple[list, bool]], list[str]]:
    """결과 파일을 읽는다. {줄 번호: ([(제품, 제품군), …], 짐작 표시)} 와 문제 목록."""
    index = json.loads((WORK / "index.json").read_text(encoding="utf-8"))
    results, problems = {}, []
    for path in sorted(WORK.glob("out_*.tsv")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            number, _, body = line.partition("\t")
            if not number.strip().isdigit() or not body.strip():
                problems.append(f"{path.name}: 읽을 수 없는 줄 {line[:60]!r}")
                continue
            number, body = int(number), body.strip()
            unsure = body.endswith("?")
            body = body.rstrip("?").strip()
            pairs = []
            if body != "-":
                for part in body.split(";"):
                    name, _, family = part.strip().rpartition("=")
                    name, family = " ".join(name.split()), " ".join(family.split())
                    if family == "기타":
                        family = REFILE.get(name, family)
                    elif family == "기타 전자부품" and name in MOTORS:
                        family = "모터"
                    elif family == "타이어 보강재":   # 제품 이름을 제품군 자리에 적은 한 줄
                        family = "타이어"
                    if not name or family not in FAMILY_FIELD:
                        problems.append(f"{path.name}: {number} 의 제품군 {family!r} 이 목록에 없습니다 ({part.strip()[:40]})")
                        continue
                    if (name, family) not in pairs:
                        pairs.append((name, family))
            if number in results:
                problems.append(f"{path.name}: {number} 이 두 번 나옵니다")
            results[number] = (pairs[:MAX_PRODUCTS], unsure)
    # 같은 제품군 안에서 띄어쓰기만 다른 이름("실리콘카바이드 부품", "실리콘 카바이드 부품")은 더 많이 쓰인 표기로 모은다
    spellings: dict[tuple, Counter] = defaultdict(Counter)
    for pairs, _ in results.values():
        for name, family in pairs:
            spellings[(family, name.replace(" ", "").lower())][name] += 1
    best = {key: max(names, key=lambda n: (names[n], " " in n, n)) for key, names in spellings.items()}
    for number, (pairs, unsure) in results.items():
        results[number] = (list(dict.fromkeys((best[(family, name.replace(" ", "").lower())], family) for name, family in pairs)), unsure)
    # 같은 제품군 안에서 같은 제품을 가리키는 다른 이름을 하나로 모은다 (merge_*.tsv: 제품군, 옮길 이름, 남길 이름).
    # 넓은 이름과 좁은 이름은 모으지 않는다. 목록에 없는 이름이나 사슬(남길 이름이 다시 옮겨짐)은 받지 않는다
    names = defaultdict(set)
    for pairs, _ in results.values():
        for name, family in pairs:
            names[family].add(name)
    moves = {}
    for path in sorted(WORK.glob("merge_*.tsv")):
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) != 3 or not all(parts):
                continue
            family, old, new = parts
            if old not in names[family] or new not in names[family] or old == new:
                problems.append(f"{path.name}: 모을 수 없는 줄 ({family} | {old} → {new})")
                continue
            moves[(family, old)] = new
    for (family, old), new in list(moves.items()):
        if (family, new) in moves:
            problems.append(f"사슬: {family} | {old} → {new} → {moves[(family, new)]}")
            del moves[(family, old)]
    for number, (pairs, unsure) in results.items():
        results[number] = (list(dict.fromkeys((moves.get((family, name), name), family) for name, family in pairs)), unsure)
    missing = [n for n in range(len(index)) if n not in results]
    if missing:
        problems.append(f"빠진 줄 {len(missing):,}개 (예: {missing[:8]})")
    return results, problems


def check():
    results, problems = read_results()
    families = Counter(family for pairs, _ in results.values() for _, family in pairs)
    print(f"결과가 있는 줄 {len(results):,}개 | 제품 없음 {sum(1 for p, _ in results.values() if not p):,} | "
          f"짐작 표시 {sum(1 for _, u in results.values() if u):,} | 제품 이름 {len({n for p, _ in results.values() for n, _ in p}):,}가지")
    print("많이 쓰인 제품군:", ", ".join(f"{k} {v}" for k, v in families.most_common(12)))
    print("쓰이지 않은 제품군:", ", ".join(f for f in FAMILY_FIELD if f not in families) or "없음")
    for problem in problems[:60]:
        print("  문제:", problem)
    print(f"문제 {len(problems):,}건")


def apply(named_by: str):
    index = json.loads((WORK / "index.json").read_text(encoding="utf-8"))
    results, problems = read_results()
    if any("빠진 줄" in p for p in problems):
        sys.exit("빠진 줄이 있어 반영하지 않습니다. check 로 확인하세요")
    by_key = {(company_id, segment, name): results[number] for number, (company_id, segment, name) in enumerate(index)}
    changed = 0
    with session(init_db()) as db:
        for row in db.scalars(select(Product)):
            found = by_key.get((row.company_id, row.segment, row.name))
            if found is None:
                continue
            pairs, unsure = found
            row.std_names, row.std_families = [name for name, _ in pairs], [family for _, family in pairs]
            row.named_by, row.unsure = named_by, unsure
            changed += 1
        db.commit()
    print(f"반영한 줄 {changed:,}개 ({named_by})")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "export":
        export()
    elif command == "check":
        check()
    elif command == "apply" and len(sys.argv) > 2:
        apply(sys.argv[2])
    else:
        sys.exit(__doc__)
