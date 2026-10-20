"""Agent가 쓰는 도구. Agent는 SQL을 쓰지 않고 이 함수들만 부른다.

조회 규칙(시점, 최신 보고서, 정정)은 query.py 가 이미 적용한다. 여기서는 Agent가 틀리기 쉬운 곳을 막는다.
  - 조회 시점(as_of)은 모든 도구에서 필수다. 빼먹으면 오류를 돌려준다
  - 기업은 find_company 가 준 company_id 로만 가리킨다. 이름으로 바로 조회하지 못한다
  - 결과는 정해진 순서로 정렬하고, 많으면 자른 뒤 전체 건수와 잘렸다는 사실을 같이 준다
  - 결과가 비었을 때 "공시된 것이 없음"인지 "모으지 않음"인지 가릴 수 있게 수집 범위를 같이 준다
  - 모든 줄에 근거 공시의 접수번호가 있다
"""
import re
import statistics
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from . import hot, query
from . import ksic as ksic_table
from .display_names import group_key, group_label
from .db import BusinessSection, Company, Document, HotDay, PriceDaily, Product, ProductCode, Relation
from .product_families import FAMILIES, FAMILY_FIELD

LIMIT = 50
GRAPH_MENTIONS = 150   # 글 검색으로만 걸린 회사는 낱말이 많이 나온 순으로 이만큼까지 그래프에 그린다
LISTED = ("Y", "K", "N")
DART_LINK = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
MARKETS = {"Y": "유가증권", "K": "코스닥", "N": "코넥스", "E": "비상장 등"}
ATTRS = ("title", "kind", "period_start", "period_end", "party_hidden", "party_relation", "subsidiary", "ratio_pct",
         "recent_sales", "region", "purpose", "method", "pct_after", "shares", "shares_after", "equity_ratio_pct",
         "expected_date", "business", "nationality", "relation", "ratio_check", "correction_reason", "changes",
         "amount_from", "reason", "listed", "relation_to_filer",
         "holder_name_raw", "shares_begin", "pct_begin", "exited", "note", "share_class", "treasury")
# detail 에 나오는 이름의 뜻. 도구 결과에 같이 실어 Agent가 값을 헷갈리지 않게 한다
FIELDS = ("as_of_date: 계약일·결의일·해지일·보고서 기준일. period_start~period_end: 계약기간(시작일~종료일). "
          "ratio_pct: 최근 매출액 대비(%). equity_ratio_pct: 자기자본 대비(%). expected_date: 취득·처분 예정일자. "
          "shares: 취득·처분 주식수, shares_after·pct_after: 거래 뒤 소유주식수·지분율. "
          "changes: 정정 공시의 [항목, 정정 전, 정정 후]. reason: 해지·철회 사유. correction_reason: 정정 사유. "
          "주주 목록(equity 의 in)에서: shares 는 기말 주식수, shares_begin·pct_begin 은 기초 주식수·지분율, "
          "holder_name_raw 는 그 보고서에 적힌 주주 이름(회사가 그 뒤 이름을 바꿨으면 지금 이름과 다르다. 그 시점을 묻는 답에는 이 이름을 쓴다), "
          "exited=true 는 기초에는 주식이 있었으나 기말에 0주가 된 주주(출자 목록에서는 기간 중에 다 처분한 출자), note 는 표의 비고. "
          "share_class 가 있으면 보통주가 아닌 줄(우선주·종류주·의결권 없는 주식)이고 그 값이 표에 적힌 주식 종류다. 보통주 기준을 물으면 share_class 가 없는 줄만 쓴다. "
          "treasury=true 는 회사가 가진 자기주식 줄이다(주주가 아니다). 지분율 0.0 은 반올림한 값이고 shares 가 있으면 주주다. "
          "reports: 같은 지분을 두 회사가 저마다 보고서에 적었을 때 표별 값. differs=true 면 두 보고서의 지분율이 서로 다르다")

# 제품군 목록을 도구 설명에 싣는다. Agent가 질문의 뜻에 맞는 제품군을 여기서 골라 넘긴다
_FAMILY_LIST = "; ".join(f"[{field}] " + ", ".join(family for f, family, _ in FAMILIES if f == field)
                         for field in dict.fromkeys(f for f, _, _ in FAMILIES) if field != "기타")
_AS_OF = {"type": "string", "description": "조회 시점 (YYYY-MM-DD). 이 날짜까지 공시된 것만 본다. 질문에 시점이 없으면 오늘 날짜"}
_COMPANY = {"type": "integer", "description": "find_company 가 돌려준 company_id"}
COMPOSITE_TOOLS = ("compare_reports", "holder_changes")   # 자주 나오는 비교 절차를 묶은 도구. 환경 변수 ASK_COMPOSITE=1 일 때만 준다
SCREEN_TOOL = "show_on_graph"   # 조회가 아니라 화면을 움직이는 도구. 환경 변수 ASK_SCREEN_TOOL=0 이면 Agent에게 주지 않는다
# 화면 도구의 views 로 띄우는 시각화 카드 (값은 views.py 가 채운다)
VIEW_KINDS = {
    "ownership": "지분 구조도. 주주와 그 위의 주주, 출자 대상",
    "group": "계열 지도. 계열회사, 상장사는 시가총액 크기",
    "supply_flow": "공급망 흐름. 누가 누구에게 얼마의 공급계약을 맺었나",
    "path": "연결 경로. other_company_id 까지 두 기업이 어떻게 이어지나",
    "timeline": "공시 타임라인. 공급계약·정정·해지·지분 취득·처분 공시를 시간 순으로",
    "contracts": "계약 기간. 계약마다 시작~종료 막대",
    "revenue": "매출 구성. 제품별 매출 비중",
    "returns": "수익률 비교. 1주·1개월·3개월 수익률을 시장·같은 제품 종목과",
    "financials": "실적 추이. 분기별 매출·영업이익 막대와 영업이익률",
    "health": "재무 체력. 부채비율·유동비율·영업이익률을 같은 시장 중앙값과",
    "compare": "실적 비교. other_company_id 와 같은 분기의 매출·영업이익·이익률·부채비율을 나란히",
    "holders": "주주 분포. 최대주주와 특수관계인 합계, 5% 이상 보고자, 나머지를 한 막대에",
    "insiders": "내부자 매매. 기간(since, 기본 90일) 동안 임원·주요주주가 늘리고 줄인 주식 수를 날짜별로, 주가 수익률 선과 함께",
    "dilution": "희석 이력. 유상증자·전환사채·신주인수권부사채·교환사채·무상증자·감자 결정을 시간 축에, 주식 수와 금액",
    "payout": "주주 환원. 사업연도마다 배당총액과 자기주식 취득 결정 금액, 주당배당금과 배당성향",
}

TOOLS = [
    {"name": "compare_reports",
     "description": "같은 지분을 두 회사가 저마다의 사업보고서에 어떻게 적었는지 한 번에 대조한다. "
                    "company_id 는 지분을 내준 회사(그 회사의 최대주주 현황), holder_id 는 지분을 가진 회사(그 회사의 타법인출자 현황). "
                    "두 표의 기초·기말 주식수와 지분율을 나란히 돌려주고, 서로 다른 칸을 differences 에 적는다. "
                    "두 보고서가 같은 숫자를 적었는지, 한 해 동안 어떻게 바뀌었는지를 물으면 get_relations 를 여러 번 부르지 말고 이것을 쓴다.",
     "input_schema": {"type": "object", "properties": {"company_id": _COMPANY, "holder_id": _COMPANY, "as_of": _AS_OF},
                      "required": ["company_id", "holder_id", "as_of"]}},
    {"name": "holder_changes",
     "description": "한 회사의 최대주주 현황(최대주주와 특수관계인, 보통주)을 두 시점에서 견주어 명단의 변동을 돌려준다: "
                    "새로 들어온 주주(entered), 명단에서 빠졌거나 0주가 된 주주(left), 주식수가 바뀐 주주(changed), 그대로인 주주(same), 두 시점의 합계 줄. "
                    "earlier 와 later 는 조회 시점(YYYY-MM-DD)이고 그날까지 나온 가장 최근 사업보고서를 쓴다. "
                    "2023 사업연도 말과 2025 사업연도 말을 견주려면 earlier=2024-06-30, later=2026-06-30 처럼 준다.",
     "input_schema": {"type": "object", "properties": {"company_id": _COMPANY,
                                                       "earlier": {"type": "string", "description": "앞 시점 (YYYY-MM-DD)"},
                                                       "later": {"type": "string", "description": "뒤 시점 (YYYY-MM-DD)"}},
                      "required": ["company_id", "earlier", "later"]}},
    {"name": "find_company",
     "description": "이름이나 6자리 종목코드로 기업을 찾는다. 다른 도구를 쓰기 전에 반드시 이것으로 company_id 를 얻는다. "
                    "후보가 여럿이면 종목코드와 시장을 보고 고르고, 고를 수 없으면 사용자에게 되묻는다.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "get_relations",
     "description": "한 기업의 관계를 조회한다. rel_type: equity(지분, 사업보고서 기준), affiliate(계열회사), "
                    "supply_contract(단일판매·공급계약 공시), supply_termination(공급계약 해지 공시), "
                    "stake_acquisition / stake_disposal(타법인 주식 취득·처분 결정). "
                    "detail.subsidiary 가 있으면 공시는 이 기업(모회사)이 냈지만 실제 계약·결정의 당사자는 그 자회사다. "
                    "direction: out 은 이 기업이 주체(지분을 가진 쪽, 판 쪽, 결정한 쪽), in 은 이 기업이 상대(지분을 내준 쪽, 산 쪽, 대상). "
                    "equity 의 in 은 이 기업의 주주 목록이다: 최대주주와 그 특수관계인(개인 포함, detail.relation_to_filer 에 본인·친인척·계열회사 등), "
                    "그리고 이 기업 지분을 가졌다고 자기 보고서에 적은 다른 회사. 최대주주 본인은 relation_to_filer 로 가린다. "
                    "지분과 계열은 그 시점에 나와 있는 가장 최근 사업보고서의 값이다. 공급계약과 취득·처분 결정은 그 시점에 유효한 공시이고 "
                    "정정 전 값이나 철회된 결정은 빠진다. 공시일 범위를 좁히려면 disclosed_from, disclosed_to 를 쓴다. "
                    "\"그 기간에 나온 공시를 모두\"처럼 공시 건수를 세는 질문에는 include_superseded=true 로, 나중에 정정된 공시까지 받는다. "
                    "결과가 잘리면(truncated) counterparty_id 나 공시일 범위로 좁혀 다시 부른다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "rel_type": {"type": "string", "enum": ["equity", "affiliate", "supply_contract", "supply_termination", "stake_acquisition", "stake_disposal"]},
         "direction": {"type": "string", "enum": ["out", "in", "both"]},
         "disclosed_from": {"type": "string", "description": "이 날짜부터 공시된 것만 (YYYY-MM-DD)"},
         "disclosed_to": {"type": "string", "description": "이 날짜까지 공시된 것만 (YYYY-MM-DD)"},
         "listed_only": {"type": "boolean", "description": "상대가 상장사인 줄만"},
         "counterparty_id": {"type": "integer", "description": "관계의 반대쪽이 이 기업인 줄만 (find_company 의 company_id)"},
         "include_superseded": {"type": "boolean", "description": "나중에 정정·해지·철회로 무효가 된 공시도 포함 "
                                                                  "(공급계약, 취득·처분 결정에만 뜻이 있다)"}},
         "required": ["company_id", "as_of", "rel_type", "direction"]}},
    {"name": "get_filings",
     "description": "한 기업이 낸 공시 목록(공급계약, 취득·처분 결정, 사업보고서)을 접수 순으로 준다. 정정·해지·철회 공시가 포함되고, "
                    "어느 공시를 고친 것인지(corrects)와 그 시점에 최신본인지가 나온다. 해지·철회 공시는 contents 에 무엇을 해지·철회했는지 "
                    "(계약명, 상대, 금액, 사유, 철회 전 값)가 들어 있고, 원래 공시가 수집 기간(2024-01) 이전이어도 이것으로 답할 수 있다. "
                    "계약이 해지됐는지, 결정이 철회됐는지, "
                    "정정 전에는 값이 무엇이었는지 확인할 때 쓴다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "doc_type": {"type": "string", "enum": ["supply_contract", "event", "annual"]},
         "filed_from": {"type": "string"}, "filed_to": {"type": "string"}},
         "required": ["company_id", "as_of", "doc_type"]}},
    {"name": "list_companies",
     "description": "조건에 맞는 기업 목록. industry 는 한국표준산업분류(KSIC) 코드의 앞자리다 (예: 30 자동차 및 트레일러, 303 자동차 부품, "
                    "26 전자부품·컴퓨터·통신장비, 21 의약품, 64 금융). group 은 공정위 대규모기업집단 이름(find_company 결과의 group 값 그대로). "
                    "업종 코드는 DART에 등록된 회사에만 있다.",
     "input_schema": {"type": "object", "properties": {
         "industry": {"type": "string"}, "group": {"type": "string"},
         "listed_only": {"type": "boolean", "description": "지금 상장된 회사만 (기본 true)"}}}},
    {"name": "find_disclosers",
     "description": "기간 안에 어떤 종류의 공시를 낸 기업을 한 번에 찾는다 (기업마다 get_relations 를 부르지 않아도 된다). "
                    "공시를 낸 쪽을 업종(industry)이나 기업집단(group)으로, 상대 쪽을 counterparty_id 나 counterparty_group 으로 좁힐 수 있다. "
                    "기업마다 새로 낸 공시 수(new)와 정정 공시 수(corrections), 접수번호를 준다. 나중에 정정된 공시도 센다.",
     "input_schema": {"type": "object", "properties": {
         "as_of": _AS_OF,
         "rel_type": {"type": "string", "enum": ["supply_contract", "supply_termination", "stake_acquisition", "stake_disposal"]},
         "disclosed_from": {"type": "string"}, "disclosed_to": {"type": "string"},
         "industry": {"type": "string", "description": "공시를 낸 기업의 KSIC 코드 앞자리"},
         "group": {"type": "string", "description": "공시를 낸 기업의 기업집단"},
         "counterparty_id": {"type": "integer", "description": "상대 기업의 company_id"},
         "counterparty_group": {"type": "string", "description": "상대 기업의 기업집단"},
         "listed_only": {"type": "boolean", "description": "공시를 낸 기업이 지금 상장된 회사인 것만 (기본 true)"}},
         "required": ["as_of", "rel_type", "disclosed_from", "disclosed_to"]}},
    {"name": "find_paths",
     "description": "두 기업을 잇는 가장 짧은 관계 경로를 찾는다 (최대 3단계, 방향 무시).",
     "input_schema": {"type": "object", "properties": {"from_company_id": _COMPANY, "to_company_id": _COMPANY, "as_of": _AS_OF},
                      "required": ["from_company_id", "to_company_id", "as_of"]}},
    {"name": "search_business",
     "description": "정기보고서(사업보고서·반기보고서 가운데 조회 시점까지 나온 가장 나중 것)의 '사업의 내용'(회사가 스스로 적은 사업 설명, 제품과 매출 비중 표, 원재료, 매출처)을 낱말로 검색해 "
                    "그 사업을 하는 회사를 찾는다. 업종 분류가 아니라 보고서의 글에서 찾으므로, 이슈나 주제와 관련된 회사를 찾을 때 쓴다. "
                    "keywords 에는 이슈 이름이 아니라 그 이슈가 닿는 제품·서비스·원재료의 이름을 여러 개 넣는다 "
                    "(예: 노벨문학상 → [\"단행본\", \"도서\", \"서점\", \"전자책\", \"인쇄용지\"]). 낱말 하나는 2~15자, 붙여 쓴 그대로 찾는다. "
                    "결과의 snippets 는 보고서에 적힌 문장 그대로이고, 낱말이 스쳐 지나간 회사도 섞여 있으니 "
                    "주력인지 일부인지는 get_business 로 매출 비중 표를 읽고 판단한다.",
     "input_schema": {"type": "object", "properties": {
         "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8,
                      "description": "찾을 낱말들. 여러 낱말에 걸리고 사업 개요나 제품 표에 나온 회사가 앞에 온다"},
         "as_of": _AS_OF,
         "listed_only": {"type": "boolean", "description": "상장사만 (기본 true)"}},
         "required": ["keywords", "as_of"]}},
    {"name": "get_business",
     "description": "한 회사의 가장 나중 정기보고서(사업보고서·반기보고서)에서 '사업의 내용'을 읽는다. 기본은 사업의 개요와 주요 제품 및 서비스(매출 비중 표). "
                    "표는 한 줄이 표의 한 줄이고 칸은 ' | ' 로 나뉜다. 매출 비중은 표에 적힌 숫자 그대로 옮긴다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "sections": {"type": "array", "items": {"type": "integer"},
                      "description": "읽을 소제목 번호. 1 사업의 개요, 2 주요 제품 및 서비스, 3 원재료 및 생산설비, 4 매출 및 수주상황, "
                                     "6 주요계약 및 연구개발활동, 7 기타 참고사항. 기본 [1, 2]"}},
         "required": ["company_id", "as_of"]}},
    {"name": "find_by_product",
     "description": "무엇을 파는 회사인지로 회사를 찾는다. 정기보고서의 '주요 제품 및 서비스' 매출 비중 표에서 읽은 줄을 찾으므로 "
                    "회사마다 매출에서 차지하는 비중(share_pct)이 바로 나온다. 비중이 큰 회사가 앞에 온다. 찾는 길은 둘이다. "
                    "(1) families: 아래 제품군 목록에서 질문의 뜻에 맞는 것을 골라 글자 그대로 넘긴다. 가장 정확하다. "
                    "제품군은 '서로 경쟁하는 회사끼리'로 나뉘어 있다. 예: 반도체를 만드는 회사는 메모리 반도체·시스템 반도체이고, "
                    "반도체 전공정 장비·반도체 소재는 반도체를 만드는 회사가 아니다. 질문이 넓으면 여러 제품군을 함께 넘긴다. "
                    "(2) keywords: 제품 이름을 여러 표기로 넘긴다 (예: [\"분리막\", \"LiBS\"]). 제품군보다 좁은 것을 찾을 때 쓴다. 둘을 함께 주면 둘 다 맞는 줄만 찾는다. "
                    "줄마다 name 과 segment 는 보고서 표에 적힌 그대로이고, products 는 그 줄에 붙인 표준 이름과 제품군이다(사람이 전부 확인한 것은 아니며 "
                    "unsure 가 true 면 짐작이 섞였다). 답에는 표에 적힌 이름과 비중을 옮긴다. "
                    "표를 읽지 못한 회사와 제품 표가 없는 회사(금융업 등)는 여기에 나오지 않으므로, 빠짐없이 찾아야 하면 search_business 도 함께 쓴다. "
                    f"제품군 목록 — {_FAMILY_LIST}",
     "input_schema": {"type": "object", "properties": {
         "families": {"type": "array", "items": {"type": "string"}, "maxItems": 8, "description": "제품군 이름들. 목록에 있는 것을 글자 그대로"},
         "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                      "description": "제품 이름들. 낱말 하나는 2~20자. 띄어쓰기는 무시하고 찾는다"},
         "ksic": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                  "description": "한국표준산업분류(11차) 숫자 코드의 앞자리(2~5자리). 사용자가 공식 분류 코드로 물을 때만 쓴다. 제품마다 붙인 세세분류 코드로 찾는다"},
         "as_of": _AS_OF,
         "min_share": {"type": "number", "description": "매출 비중이 이 값(%) 이상인 회사만. 주력인 회사만 볼 때 쓴다"},
         "listed_only": {"type": "boolean", "description": "상장사만 (기본 true)"}},
         "required": ["as_of"]}},
    {"name": "get_products",
     "description": "한 회사의 제품과 매출 비중을 표로 읽은 그대로 준다 (가장 나중 정기보고서의 '주요 제품 및 서비스' 표). "
                    "읽지 못한 회사는 read=false 로 나오고, 그때는 get_business 로 표의 글을 직접 읽는다.",
     "input_schema": {"type": "object", "properties": {"company_id": _COMPANY, "as_of": _AS_OF},
                      "required": ["company_id", "as_of"]}},
    {"name": "get_financials",
     "description": "한 회사의 실적과 재무 상태 (정기보고서의 주요계정, 상장사만). "
                    "quarters: 분기마다 석 달의 매출·영업이익·순이익, 영업이익률, 전년 같은 분기 대비 증감(yoy_pct, 앞이 적자면 yoy 에 흑자 전환 등). "
                    "4분기는 공시되지 않아 연간에서 3분기 누적을 뺀 값이고 computed 로 밝힌다. years: 사업연도마다 가장 나중 보고서의 누적 값(예: '2026 상반기 누적'). "
                    "balance: 가장 최근 기말과 직전 사업연도 말의 자산·부채·자본, 부채비율(부채총계/자본총계), 유동비율(유동자산/유동부채). "
                    "market_median: 같은 시장 상장사들의 부채비율·유동비율·영업이익률 중앙값. 금액은 '89조 4,924억 원' 같은 글로 준다. "
                    "basis 는 연결(CFS, 기본)과 별도(OFS). 지주회사처럼 둘이 크게 다를 수 있으니 답에 어느 쪽인지 적는다. "
                    "현금흐름, EPS, 배당, PER·PBR, 사업부문별 실적, 실적 전망은 이 DB에 없다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "basis": {"type": "string", "enum": ["CFS", "OFS"], "description": "CFS 연결(기본, 없으면 별도), OFS 별도"}},
         "required": ["company_id", "as_of"]}},
    {"name": "get_major_holders",
     "description": "한 회사의 5% 대량보유 상황보고(5% 보고). holders: 보고자마다 조회 시점까지의 가장 최근 보고(보유비율 pct, 주식수, 그 보고의 증감, 보고 사유). "
                    "status 가 '5% 미만으로 내려감'이면 마지막 보고에서 5% 아래가 된 것이다. recent_reports: since(기본 1년 전)부터의 보고를 최근 것부터. "
                    "new_holder 는 보고 사유가 신규 보고인 것(새로 5% 이상이 됨). 비율은 대표보고자와 특별관계자를 합친 값이라, 서로 특별관계인 보고자의 비율을 더하면 겹칠 수 있다. "
                    "최대주주와 특수관계인의 지분은 get_relations(equity, in) 이 따로 준다. OpenDART 가 주는 보고는 2024-10 부터다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "since": {"type": "string", "description": "recent_reports 의 시작일 YYYY-MM-DD (기본 as_of 의 1년 전)"}},
         "required": ["company_id", "as_of"]}},
    {"name": "get_insider_trades",
     "description": "한 회사의 임원·주요주주 특정증권등 소유상황보고(내부자의 주식 증감). 기간(기본: as_of 를 포함한 최근 90일) 안에 접수된 보고를 모은다. "
                    "totals: 늘린 보고 건수와 주식 수, 줄인 보고 건수와 주식 수, 순증감, 보고한 사람 수. reporters: 사람마다 기간 증감 합계, 보고 뒤 소유 주식수와 비율, 직위, 등기 여부, 주요주주 구분. "
                    "날짜는 실제 거래일이 아니라 보고 접수일이다. 거래 단가와 금액, 장내·장외 구분, 증여·상속 같은 사유는 이 보고 자료에 없다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "since": {"type": "string", "description": "기간 시작일 YYYY-MM-DD (기본 as_of 의 89일 전, 그래서 as_of 까지 90일)"},
         "until": {"type": "string", "description": "기간 끝날 YYYY-MM-DD (기본 as_of). '9월 한 달'처럼 물으면 since 와 until 을 함께 준다"}},
         "required": ["company_id", "as_of"]}},
    {"name": "get_capital_events",
     "description": "한 회사의 자본 변동 결정 공시: 유상증자, 무상증자, 감자, 전환사채(CB), 신주인수권부사채(BW), 교환사채(EB), 자기주식 취득·취득 신탁계약·처분. "
                    "events: 결정마다 접수일, 종류, 주식 수(새 주식, 사채가 전환될 주식, 취득·처분·감자 주식), 결정 전 발행주식총수, 금액(조달액, 사채 권면총액, 취득·처분 예정액), "
                    "전환·교환·처분 가액, 발행주식총수 대비 비율, 방식(공모·사모·제3자배정, 장내매수), 목적, 기간. by_kind: 종류마다 건수와 합계. "
                    "결정이지 실행 결과가 아니다: 실제 납입·전환·취득 결과, 철회는 없다. 자기주식 소각은 감자결정으로 공시한 것만 있다(treasury_cancel). "
                    "date 는 마지막 정정 공시의 접수일일 수 있다. 2024-01 부터 모았다.",
     "input_schema": {"type": "object", "properties": {
         "company_id": _COMPANY, "as_of": _AS_OF,
         "since": {"type": "string", "description": "시작일 YYYY-MM-DD (기본 2024-01-01, 수집 시작)"},
         "kinds": {"type": "array", "items": {"type": "string", "enum": ["rights_issue", "bonus_issue", "rights_bonus_issue", "capital_reduction",
                                                                       "cb", "bw", "eb", "buyback", "buyback_trust", "treasury_sale"]},
                   "description": "볼 종류. 없으면 모두"}},
         "required": ["company_id", "as_of"]}},
    {"name": "get_dividends",
     "description": "한 회사의 배당(사업보고서의 배당에 관한 사항)과 자기주식 취득 결정을 사업연도마다. "
                    "years: 주당 현금배당금(보통주·우선주), 현금배당금총액, 현금배당성향(%), 현금배당수익률(%), 주당순이익. "
                    "buybacks: 그해 공시한 자기주식 취득 결정과 취득 신탁계약의 예정 금액 합계(결정 금액이지 실제 취득액이 아니다). "
                    "2023~2025 사업연도. 분기·중간배당의 회차별 금액과 앞으로의 배당 예상은 없다.",
     "input_schema": {"type": "object", "properties": {"company_id": _COMPANY, "as_of": _AS_OF}, "required": ["company_id", "as_of"]}},
    {"name": "get_price_history",
     "description": "한 회사의 기간 수익률을 시장, 같은 제품을 파는 종목들과 견준다: 최근 1주, 1개월, 3개월. "
                    "\"요즘 주가가 어땠나\", \"정말 못 올랐나\", \"시장보다 나았나\"를 확인할 때 쓴다. 가격 자체는 주지 않고 수익률(%)만 준다. "
                    "장 마감 뒤의 일별 값이며 장중 값이 아니다. 보관한 시세는 최근 약 90일이라 그보다 긴 기간은 없다.",
     "input_schema": {"type": "object", "properties": {"company_id": _COMPANY, "as_of": _AS_OF}, "required": ["company_id", "as_of"]}},
    {"name": "get_price_moves",
     "description": "거래일의 주가 움직임을 관계와 제품으로 설명한다. 그날 시장보다 크게 오르거나 내린 종목 가운데, 같은 제품을 팔거나 "
                    "지분·계열·공급계약으로 이어진 종목을 묶은 '종목군'과, 어디에도 묶이지 않은 '개별 종목'을 준다. 장 마감 뒤의 일별 값이며 장중 값이 아니다. "
                    "쓰는 법 셋: (1) day 만 주면 그날의 전체, (2) company_id 를 주면 그 회사가 급등·급락한 날과 함께 움직인 종목, "
                    "(3) keyword 를 주면 연결 고리(제품 이름)나 종목 이름에 그 낱말이 든 종목군이 나온 날. (2)와 (3)은 as_of 에서 lookback_days 만큼 거슬러 찾는다.",
     "input_schema": {"type": "object", "properties": {
         "as_of": _AS_OF,
         "day": {"type": "string", "description": "볼 거래일 YYYY-MM-DD. 없으면 as_of 이전의 가장 최근 거래일. 그날이 거래일이 아니면 바로 앞 거래일"},
         "company_id": _COMPANY,
         "keyword": {"type": "string", "description": "연결 고리나 종목 이름에서 찾을 낱말. 예: 강관, HLB, 전력 케이블"},
         "direction": {"type": "string", "enum": ["up", "down", "both"], "description": "오른 쪽, 내린 쪽, 둘 다(기본)"},
         "lookback_days": {"type": "integer", "description": "company_id 나 keyword 로 찾을 때 거슬러 볼 날 수(달력 기준). 기본 30, 최대 120"}},
         "required": ["as_of"]}},
    {"name": SCREEN_TOOL,
     "description": "화면에 무엇을 보일지 정한다. 조회를 마친 뒤, 답의 글을 쓰기 시작하기 전에 이 도구만 따로 한 번 부른다(같은 차례에 글을 쓰지 않는다). 부르면 사용자의 화면에서 그 기업들이 바로 떠오른다. "
                    "highlight 는 답이 가리키는 기업(이름표가 강조된다), also 는 맥락으로 함께 그릴 기업이다. "
                    "답에서 제외했다고 적을 기업은 어느 쪽에도 넣지 않는다. 부르지 않으면 조회 결과에 나온 기업이 어림으로 그려진다. "
                    "views 는 답 위에 띄울 시각화 카드(둘까지)다. 답이 구조나 흐름, 여러 줄의 목록을 다루면 그 모양에 맞는 카드를 고른다. "
                    "카드의 숫자는 서버가 DB에서 채우므로 종류와 대상만 정한다. 값 하나나 둘로 끝나는 답이면 views 를 비운다.",
     "input_schema": {"type": "object", "properties": {
         "highlight": {"type": "array", "items": {"type": "integer"}, "description": "강조할 기업의 company_id. 1~12곳"},
         "also": {"type": "array", "items": {"type": "integer"}, "description": "함께 그릴 기업의 company_id. 최대 40곳. 없어도 된다"},
         "views": {"type": "array", "maxItems": 2,
                   "description": "답 위에 띄울 카드. 넣는 때: 답이 여러 기간의 흐름, 여러 회사·주주·계약의 목록이나 구조, 두 회사의 비교를 다룰 때. "
                                  "비우는 때: 한 기간의 값 하나나 둘을 묻는 질문(예: 작년 순이익, 1분기 매출, 유동비율, 본사 주소), "
                                  "한 주주의 지분율이나 한 사람의 주식 증감을 묻는 질문(예: 어느 연기금의 보유비율, 사외이사 한 명이 판 주식), "
                                  "이 DB에 없는 것을 물어 대신 다른 사실을 알려 주는 답(예: 배당, 목표주가, 다음 해 전망), 매매 판단을 묻는 질문. "
                                  "그 경우 조회 결과에 추이가 있어도 카드를 넣지 않는다", "items": {"type": "object", "properties": {
             "kind": {"type": "string", "enum": list(VIEW_KINDS),
                      "description": "; ".join(f"{k}: {v}" for k, v in VIEW_KINDS.items())},
             "company_id": _COMPANY, "as_of": _AS_OF,
             "other_company_id": {"type": "integer", "description": "path·compare 일 때 상대 기업의 company_id"},
             "since": {"type": "string", "description": "supply_flow·timeline·contracts 의 시작일(기본 as_of 의 1년 전), insiders 의 시작일(기본 90일) YYYY-MM-DD"}},
             "required": ["kind", "company_id", "as_of"]}}},
         "required": ["highlight"]}},
    {"name": "get_coverage",
     "description": "이 DB가 무엇을 언제부터 언제까지 모았고 무엇을 모으지 않았는지. 결과가 비었을 때 답하기 전에 확인한다.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _detail(attrs: dict | None, keys=ATTRS) -> dict:
    """공시에서 읽은 값 가운데 Agent에게 내줄 것. 정정 내역은 길어질 수 있어 앞의 여덟 줄, 칸마다 120자까지만 싣는다."""
    out = {k: (attrs or {})[k] for k in keys if (attrs or {}).get(k) not in (None, "", False)}
    if isinstance((attrs or {}).get("listed"), bool):   # 계열회사 표의 상장·비상장 구분
        out["listed"] = "상장" if attrs["listed"] else "비상장"
    if "changes" in out:
        out["changes"] = [[str(cell)[:120] for cell in row] for row in out["changes"][:8]]
    return out


class ToolError(ValueError):
    """Agent에게 그대로 돌려줄 오류. 무엇을 고쳐 다시 부르면 되는지 적는다."""


def _date(value, name: str, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise ToolError(f"{name} 이 없습니다. YYYY-MM-DD 로 주세요")
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolError(f"{name}={value!r} 를 날짜로 읽을 수 없습니다. YYYY-MM-DD 로 주세요") from None


def _company(db, company_id) -> Company:
    company = db.get(Company, company_id) if isinstance(company_id, int) else None
    if company is None:
        raise ToolError(f"company_id={company_id!r} 인 기업이 없습니다. find_company 로 먼저 찾으세요")
    return company


def _brief(company: Company | None) -> dict | None:
    if company is None:
        return None
    return {"company_id": company.company_id, "name": company.label, "stock_code": company.stock_code,
            "market": MARKETS.get(company.corp_cls, "원장에 이름만 있음"), "group": group_label(company.ftc_group)}


def find_company(db, name: str) -> dict:
    found = query.find_companies(db, name.strip())
    return {"candidates": [_brief(c) for c in found],
            "note": "group 은 공정위 2026년 5월 지정 대규모기업집단 이름입니다. group 이 null 이면 지정 집단 소속이 아닙니다"
                    if found else "원장에 없는 이름입니다. 상장사와 그 계열회사만 들어 있습니다"}


_TABLES = {"largest_shareholders": "최대주주 현황", "other_corp_investments": "타법인출자 현황"}


def _edge(db, edge: dict) -> dict:
    subject = db.get(Company, edge["subject_id"]) if edge["subject_id"] else None
    obj = db.get(Company, edge["object_id"]) if edge["object_id"] else None
    value = edge["value"]
    return {"type": query.LABELS[edge["type"]],
            "subject": _brief(subject) or {"company_id": None, "name": edge["subject_name_raw"],
                                           "note": "원장에 없는 주주 (개인, 정부, 비상장·해외 법인 등. 이름만 있음)"},
            "object": _brief(obj) or {"company_id": None, "name": edge["object_name_raw"], "note": "원장에 없는 상대 (이름만 있음)"},
            "value": None if value is None else (float(value) if edge["unit"] == "pct" else int(value)),
            "unit": {"pct": "%", "krw": "원"}.get(edge["unit"]),
            "as_of_date": edge["as_of_date"] and edge["as_of_date"].isoformat(),
            "disclosed_date": edge["disclosed_date"].isoformat(),
            "disclosed_by": "양쪽 공시에 있으나 값이 다름" if edge.get("differs") else
                            {"subject": "주체가 공시", "object": "상대가 공시", "both": "양쪽 공시에서 확인"}[edge["disclosed_by"]],
            **({"reports": [{"table": _TABLES.get(r["source"], r["source"]), "filer": (subject if r["side"] == "subject" else obj).label,
                             "value": float(r["value"]), "shares": r["shares"], "rcept_no": r["rcept_no"]} for r in edge["reports"]],
                "differs": edge["differs"]} if edge.get("reports") else {}),
            "stale": edge["stale"] or None,
            "superseded_on": edge["invalidated_date"] and edge["invalidated_date"].isoformat(),
            "detail": _detail(edge["attrs"]),
            "rcept_no": edge["evidence"]}


def get_relations(db, company_id, as_of, rel_type: str, direction: str, disclosed_from=None, disclosed_to=None,
                  listed_only: bool = False, counterparty_id=None, include_superseded: bool = False) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    if rel_type not in query.LABELS:
        raise ToolError(f"rel_type={rel_type!r} 은 없습니다")
    if direction not in ("out", "in", "both"):
        raise ToolError("direction 은 out, in, both 중 하나입니다")
    start, end = _date(disclosed_from, "disclosed_from"), _date(disclosed_to, "disclosed_to")
    if rel_type == "affiliate" and direction == "out":
        edges = query.group_members(db, company.company_id, when)
    else:
        edges = query.relations(db, when, company_ids=[company.company_id], rel_types=[rel_type], direction=direction,
                                include_superseded=bool(include_superseded) and rel_type not in ("equity", "affiliate"),
                                include_exited=rel_type == "equity")
    if counterparty_id is not None:
        other = _company(db, counterparty_id).company_id
        edges = [e for e in edges if other in (e["subject_id"], e["object_id"])]
    edges = [e for e in edges if (start is None or e["disclosed_date"] >= start) and (end is None or e["disclosed_date"] <= end)]
    rows = [_edge(db, e) for e in edges]
    # 최대주주 현황 표의 합계 줄 (그 표가 적은 값. 줄마다의 지분율을 더한 값과 다를 수 있다)
    mine = [e for e in edges if e["type"] == "equity" and e["object_id"] == company.company_id and e["attrs"].get("table_total")] if direction == "in" else []
    holder_total = next((e["attrs"]["table_total"] for e in mine if not e["attrs"].get("share_class")), None)
    class_totals = {e["attrs"]["share_class"]: e["attrs"]["table_total"] for e in mine if e["attrs"].get("share_class")}
    unknown_listing = None
    if listed_only:
        other = "object" if direction != "in" else "subject"
        # 원장에 없는 상대라도 보고서가 스스로 '상장'이라고 적었으면 남긴다 (해외 상장사가 여기에 든다)
        kept = [r for r in rows if r[other].get("market") in ("유가증권", "코스닥", "코넥스") or r["detail"].get("listed") == "상장"]
        unknown_listing = sum(1 for r in rows if r not in kept and r[other]["company_id"] is None and "listed" not in r["detail"])
        rows = kept
    if rel_type == "equity":   # 지분은 큰 것부터
        rows.sort(key=lambda r: (-(r["value"] or 0), r["subject"]["name"] or "", r["object"]["name"] or ""))
    else:
        rows.sort(key=lambda r: (r["disclosed_date"], r["rcept_no"][0], r["object"]["name"]))
    sources = query.coverage(db)
    summary = None
    if rel_type == "affiliate" and direction == "out":
        listed = sum(1 for r in rows if r["detail"].get("listed") == "상장")
        report = db.scalars(select(Document).where(Document.company_id == company.company_id, Document.doc_type == "annual",
                                                   Document.rcept_dt <= when).order_by(Document.rcept_no.desc()).limit(1)).first()
        self_listed = MARKETS.get(company.corp_cls) in ("유가증권", "코스닥", "코넥스")
        summary = ({"self": f"{company.label} 자신은 {'상장사' if self_listed else '비상장사'}이고 아래 relations 목록에는 들어 있지 않습니다",
                    "excluding_self": {"listed": listed, "unlisted": len(rows) - listed},
                    "including_self": {"listed": listed + self_listed, "unlisted": len(rows) - listed + (not self_listed)},
                    "note": "보고서의 계열회사 표는 보통 자신을 넣어 셉니다. 상장·비상장 수를 물으면 including_self 를 답하고, "
                            "자신을 뺀 수(excluding_self)도 함께 밝히세요. 상장으로 분류된 회사를 물으면 자신도 듭니다"}
                   if rows else
                   {"listed": 0, "unlisted": 0,
                    "note": f"사업보고서({report.rcept_no})를 읽었고 계열회사 표에 올라온 회사가 없습니다. 보고서 기준으로 계열회사가 없다고 답할 수 있습니다"}
                   if report else None)
    return {"company": _brief(company), "as_of": when.isoformat(), "total": len(rows), "truncated": len(rows) > LIMIT,
            "affiliate_summary": summary,
            **({"holder_table_total": {**holder_total, "note": "최대주주와 특수관계인의 합계(보통주). pct 는 기말, pct_begin 은 기초"}} if holder_total else {}),
            **({"holder_table_total_other_classes": class_totals} if class_totals else {}),
            "relations": rows[:LIMIT],
            "fields": FIELDS,
            "unknown_listing": unknown_listing and f"원장에 없는 상대 {unknown_listing}곳은 상장 여부를 알 수 없어 뺐습니다. "
                                                   "해외 상장사일 수 있으니 listed_only 없이 다시 조회해 이름을 확인하세요",
            "coverage": {"source": sources["sources"].get(rel_type), **{k: str(v) for k, v in
                         sources["relations"].get(rel_type, {}).items() if k in ("first_disclosed", "last_disclosed")}},
            "note": query.NOTICE if not rows else (
                "이 목록에 조회한 기업 자신은 들어 있지 않습니다. 같은 집단의 회사를 세거나 나열할 때는 자신을 더하세요"
                if rel_type == "affiliate" and direction == "out" else None)}


def _count(text) -> int | None:
    digits = re.sub(r"[^0-9]", "", str(text or ""))
    return int(digits) if digits else None


def _ratio(text) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def compare_reports(db, company_id, holder_id, as_of) -> dict:
    company, holder, when = _company(db, company_id), _company(db, holder_id), _date(as_of, "as_of", required=True)
    rows = [r for r in query.current_rows(db, when, [company.company_id], ["equity"], "in")
            if r.subject_company_id == holder.company_id and not any((r.attrs or {}).get(k) for k in query.TABLE_ONLY)]
    if not rows:
        return {"company": _brief(company), "holder": _brief(holder), "reports": [],
                "note": "그 시점의 사업보고서에서 이 지분을 적은 표를 찾지 못했습니다. find_company 로 두 회사를 다시 확인하거나 get_relations 로 주주 목록을 보세요"}
    latest = max(r.as_of_date for r in rows)
    reports = []
    for r in sorted((r for r in rows if r.as_of_date == latest), key=lambda r: (r.attrs or {}).get("source") != "largest_shareholders"):
        a = r.attrs or {}
        mine = a.get("source") == "largest_shareholders"
        reports.append({"table": _TABLES.get(a.get("source"), a.get("source")), "filer": (company if mine else holder).label,
                        "rcept_no": r.rcept_no, "as_of_date": r.as_of_date.isoformat(),
                        "name_in_report": a.get("holder_name_raw") if mine else r.object_name_raw,
                        "begin": {"shares": _count(a.get("shares_begin")), "pct": _ratio(a.get("pct_begin"))},
                        "end": {"shares": _count(a.get("shares")), "pct": float(r.value_num)}})
    tables = {r["table"] for r in reports}
    differences, same = [], []
    if len(tables) == 2:
        first, second = next(r for r in reports if r["table"] == "최대주주 현황"), next(r for r in reports if r["table"] == "타법인출자 현황")
        for when_name, key in (("기초", "begin"), ("기말", "end")):
            for what, field in (("주식수", "shares"), ("지분율", "pct")):
                a, b = first[key][field], second[key][field]
                label = f"{when_name} {what}"
                if a is None or b is None:
                    differences.append(f"{label}: 한쪽 표에 값이 없음 ({first['filer']} {a}, {second['filer']} {b})")
                elif a != b:
                    differences.append(f"{label}: {first['filer']}의 최대주주 현황 {a}, {second['filer']}의 타법인출자 현황 {b}")
                else:
                    same.append(label)
    return {"company": _brief(company), "holder": _brief(holder), "as_of": when.isoformat(), "reports": reports,
            "both_tables_found": len(tables) == 2, "differences": differences, "same": same,
            "note": "begin 은 그 보고서의 기초 칸, end 는 기말 칸에 적힌 값입니다. 지분율은 표에 적힌 그대로이며 두 회사가 다른 기준(발행주식총수, 의결권 있는 주식)으로 적을 수 있습니다. "
                    "한쪽 표만 있으면 both_tables_found 가 false 이고 대조할 수 없습니다"}


def _holder_list(db, company: Company, when: date) -> tuple[dict, dict | None, str | None]:
    """그 시점의 최대주주 현황: 이름 → 줄, 합계 줄, 접수번호."""
    rows = [r for r in query.current_rows(db, when, [company.company_id], ["equity"], "in")
            if (r.attrs or {}).get("source") == "largest_shareholders" and not (r.attrs or {}).get("share_class") and not (r.attrs or {}).get("treasury")]
    out, total, rcept = {}, None, None
    for r in rows:
        a = r.attrs or {}
        name = a.get("holder_name_raw") or r.subject_name_raw
        out[name] = {"name": name, "relation_to_filer": " ".join((a.get("relation_to_filer") or "").split()) or None,
                     "shares": _count(a.get("shares")) or 0, "pct": float(r.value_num), "shares_begin": _count(a.get("shares_begin")),
                     "exited": bool(a.get("exited")), "note": a.get("note")}
        total, rcept = a.get("table_total") or total, r.rcept_no
    return out, total, rcept


def holder_changes(db, company_id, earlier, later) -> dict:
    company = _company(db, company_id)
    first, second = _date(earlier, "earlier", required=True), _date(later, "later", required=True)
    if first >= second:
        raise ToolError("earlier 는 later 보다 앞선 날짜여야 합니다")
    (before, total_a, rcept_a), (after, total_b, rcept_b) = _holder_list(db, company, first), _holder_list(db, company, second)
    if not before or not after:
        return {"company": _brief(company), "found": {"earlier": bool(before), "later": bool(after)},
                "note": "두 시점 가운데 최대주주 현황이 없는 쪽이 있습니다. 주주 자료는 사업보고서에서만 옵니다"}
    if rcept_a == rcept_b:
        return {"company": _brief(company), "note": f"두 시점이 같은 사업보고서({rcept_a})를 가리킵니다. 날짜를 한 해 이상 벌려 주세요"}
    held = lambda row: row is not None and row["shares"] > 0
    moved = lambda n: (before[n]["shares"], before[n]["pct"]) != (after[n]["shares"], after[n]["pct"])
    both = [n for n in after if held(after[n]) and held(before.get(n))]
    return {"company": _brief(company),
            "earlier": {"as_of": first.isoformat(), "rcept_no": rcept_a, "table_total": total_a},
            "later": {"as_of": second.isoformat(), "rcept_no": rcept_b, "table_total": total_b},
            "entered": [after[n] for n in after if held(after[n]) and not held(before.get(n))],
            "left": [{**before[n], "later": "명단에 없음" if n not in after else "0주"} for n in before if held(before[n]) and not held(after.get(n))],
            "changed": [{"name": n, "relation_to_filer": after[n]["relation_to_filer"], "shares_earlier": before[n]["shares"],
                         "shares_later": after[n]["shares"], "pct_earlier": before[n]["pct"], "pct_later": after[n]["pct"]} for n in both if moved(n)],
            "same": [{"name": n, "shares": after[n]["shares"], "pct": after[n]["pct"], "relation_to_filer": after[n]["relation_to_filer"]}
                     for n in both if not moved(n)],
            "already_zero_at_earlier": [n for n in before if not held(before[n])],
            "note": "보통주 줄만 견줍니다(우선주·종류주 줄은 들어 있지 않습니다). 이름은 보고서에 적힌 그대로 맞추므로, 회사가 이름을 바꿨으면 entered 와 left 에 따로 나올 수 있습니다. "
                    "already_zero_at_earlier 는 앞 시점에 이미 0주였던 이름으로, 빠진 사람으로 세지 않습니다. 지분율 0.0 은 반올림한 값입니다"}


def get_filings(db, company_id, as_of, doc_type: str, filed_from=None, filed_to=None) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    start, end = _date(filed_from, "filed_from"), _date(filed_to, "filed_to")
    docs = db.scalars(select(Document).where(Document.company_id == company.company_id, Document.doc_type == doc_type,
                                             Document.rcept_dt <= when).order_by(Document.rcept_no)).all()
    superseded = {d.corrects_rcept_no for d in docs if d.corrects_rcept_no}
    rows = []
    for doc in docs:
        if (start and doc.rcept_dt < start) or (end and doc.rcept_dt > end):
            continue
        relations = db.scalars(select(Relation).where(Relation.rcept_no == doc.rcept_no, Relation.retired_at.is_(None))).all()
        rows.append({"rcept_no": doc.rcept_no, "filed": doc.rcept_dt.isoformat(), "report": doc.report_nm,
                     "is_correction": bool(doc.is_correction), "corrects": doc.corrects_rcept_no,
                     "superseded_by_later_filing": doc.rcept_no in superseded,
                     "contents": [{"counterparty": r.object_name_raw, "value": None if r.value_num is None else float(r.value_num),
                                   "unit": r.value_unit, "kind": query.LABELS[r.rel_type],
                                   "as_of_date": r.as_of_date and r.as_of_date.isoformat(),
                                   **_detail(r.attrs, ATTRS + ("withdrawn", "before_withdrawal", "original_filed",
                                                               "terminated_filing", "withdrawn_filing"))}
                                  for r in relations if r.rel_type not in ("equity", "affiliate")][:5],
                     "url": DART_LINK + doc.rcept_no})
    return {"company": _brief(company), "as_of": when.isoformat(), "total": len(rows), "truncated": len(rows) > LIMIT,
            "filings": rows[-LIMIT:],
            "fields": FIELDS,
            "note": "공급계약과 취득·처분 결정은 2024-01 이후 공시만, 사업보고서는 2023~2025 사업연도만 있습니다"}


def find_paths(db, from_company_id, to_company_id, as_of) -> dict:
    start, goal, when = _company(db, from_company_id), _company(db, to_company_id), _date(as_of, "as_of", required=True)
    found = query.paths(db, start.company_id, goal.company_id, when)
    return {"from": _brief(start), "to": _brief(goal), "as_of": when.isoformat(), "total": len(found),
            "paths": [[_edge(db, e) for e in path] for path in found[:10]],
            "note": None if found else "3단계 안에서 잇는 공시된 관계가 없습니다"}


def _in_industry(company: Company, prefix: str | None) -> bool:
    # 공정위 자료의 업종코드는 "C30121"처럼 대분류 글자가 앞에 붙는다
    return not prefix or re.sub(r"^[A-Z]", "", company.induty_code or "").startswith(prefix.strip())


def list_companies(db, industry: str | None = None, group: str | None = None, listed_only: bool = True) -> dict:
    if not industry and not group:
        raise ToolError("industry 나 group 중 하나는 주어야 합니다")
    conditions = [Company.corp_cls.in_(LISTED)] if listed_only else []
    if group:
        conditions.append(Company.ftc_group == group_key(group))
    found = sorted((c for c in db.scalars(select(Company).where(*conditions)) if _in_industry(c, industry)),
                   key=lambda c: (c.label, c.company_id))
    return {"total": len(found), "truncated": len(found) > 300,
            "companies": [{**_brief(c), "industry_code": c.induty_code} for c in found[:300]],
            "note": None if found else "조건에 맞는 기업이 없습니다. group 은 find_company 결과의 group 값 그대로 써야 합니다"}


def find_disclosers(db, as_of, rel_type: str, disclosed_from, disclosed_to, industry: str | None = None,
                    group: str | None = None, counterparty_id=None, counterparty_group: str | None = None,
                    listed_only: bool = True) -> dict:
    when = _date(as_of, "as_of", required=True)
    start, end = _date(disclosed_from, "disclosed_from", required=True), _date(disclosed_to, "disclosed_to", required=True)
    if rel_type not in ("supply_contract", "supply_termination", "stake_acquisition", "stake_disposal"):
        raise ToolError("rel_type 은 supply_contract, supply_termination, stake_acquisition, stake_disposal 중 하나입니다")
    target = aliased(Company)
    conditions = [Relation.rel_type == rel_type, Relation.retired_at.is_(None), Relation.disclosed_date >= start,
                  Relation.disclosed_date <= min(end, when)]
    if counterparty_id is not None:
        conditions.append(Relation.object_company_id == _company(db, counterparty_id).company_id)
    statement = select(Relation, Document.is_correction).join(Document, Document.rcept_no == Relation.rcept_no)
    if counterparty_group:
        statement = statement.join(target, target.company_id == Relation.object_company_id)
        conditions.append(target.ftc_group == group_key(counterparty_group))
    by_company: dict[int, dict] = {}
    for relation, is_correction in db.execute(statement.where(*conditions)):
        filer = db.get(Company, relation.subject_company_id)
        if (listed_only and filer.corp_cls not in LISTED) or (group and filer.ftc_group != group_key(group)) or not _in_industry(filer, industry):
            continue
        entry = by_company.setdefault(filer.company_id, {**_brief(filer), "new": set(), "corrections": set(), "counterparties": set()})
        entry["corrections" if is_correction else "new"].add(relation.rcept_no)
        entry["counterparties"].add(relation.object_name_raw)
    rows = sorted(by_company.values(), key=lambda e: (e["name"], e["company_id"]))
    for entry in rows:
        new, corrections = sorted(entry["new"]), sorted(entry["corrections"])
        entry.update(new=len(new), corrections=len(corrections), rcept_no=(new + corrections)[:12],
                     counterparties=sorted(entry["counterparties"])[:8])
    return {"as_of": when.isoformat(), "period": [start.isoformat(), end.isoformat()], "total": len(rows),
            "with_new_filing": sum(1 for e in rows if e["new"]), "truncated": len(rows) > 200, "companies": rows[:200],
            "note": "new 는 정정이 아닌 공시, corrections 는 정정 공시의 건수입니다. 상대를 가린 공시는 counterparty 조건에 잡히지 않습니다"}


def get_coverage(db) -> dict:
    covered = query.coverage(db)
    return {"notice": covered["notice"], "sources": covered["sources"], "group": covered["sources"]["group"],
            "relations": {v["label"]: {"first_disclosed": str(v["first_disclosed"]), "last_disclosed": str(v["last_disclosed"]),
                                       "rows": v["rows"]} for v in covered["relations"].values()},
            "business": "사업 내용(search_business, get_business)과 제품(find_by_product, get_products)은 상장사의 2025 사업보고서와 2026 반기보고서에서 왔다. "
                        "제품은 매출 비중 표를 읽을 수 있었던 회사만 있다",
            "capital": "증자·감자·사채·자기주식 결정(get_capital_events)은 상장사의 2024-01 이후 주요사항보고서, 배당(get_dividends)은 2025 사업보고서의 2023~2025 사업연도다. "
                       "실제 납입·전환·취득 결과, 철회, 자기주식 소각 결정, 분기배당 회차별 금액은 없다",
            "holdings": "5% 대량보유 보고(get_major_holders)와 임원·주요주주 소유보고(get_insider_trades)는 상장사의 2024-10 이후 보고다(OpenDART 가 주는 범위). "
                        "거래 단가·금액, 실제 거래일, 매매 사유, 기관·외국인 수급, 공매도는 없다",
            "financials": "실적과 재무 상태(get_financials)는 상장사의 2022년 1분기보고서부터 가장 최근 정기보고서까지의 주요계정(매출, 영업이익, 순이익, 자산·부채·자본)이다. "
                          "현금흐름, EPS, 배당, 사업부문별 실적은 없다",
            "not_collected": ["5% 미만 주주와 소액주주", "2024-10 이전의 5% 보고와 임원 소유보고", "개인이 가진 다른 회사 지분",
                              "분기·반기보고서의 지분·계열 표", "현금흐름표, EPS, 배당, 사업부문별 실적", "주요 고객(사업보고서 본문 서술)", "뉴스", "주가",
                              "2024-01 이전의 공급계약과 취득·처분 결정"]}


_SECTION_WEIGHT = {1: 3, 2: 3, 4: 2, 6: 2}   # 사업 개요와 제품 표에 나온 낱말을 더 무겁게 친다. 나머지는 1


def _latest_business(db, when: date, company_id: int | None = None):
    """조회 시점까지 나온 사업보고서 가운데 회사마다 가장 나중 것의 접수번호."""
    latest = select(BusinessSection.company_id, func.max(BusinessSection.rcept_no).label("rcept_no")).where(
        BusinessSection.disclosed_date <= when)
    if company_id is not None:
        latest = latest.where(BusinessSection.company_id == company_id)
    return latest.group_by(BusinessSection.company_id).subquery()


def _report_name(db, rcept_no: str) -> str | None:
    """어느 보고서의 글인지. 예: "반기보고서 (2026.06)". 반기보고서의 매출은 반년 치라는 것을 읽는 쪽이 알아야 한다."""
    doc = db.get(Document, rcept_no)
    return doc and doc.report_nm.strip()


def search_business(db, keywords, as_of, listed_only: bool = True) -> dict:
    when = _date(as_of, "as_of", required=True)
    words = list(dict.fromkeys(str(w).strip() for w in (keywords if isinstance(keywords, list) else [keywords]) if str(w).strip()))
    if not words or any(not 2 <= len(w) <= 15 for w in words):
        raise ToolError("keywords 는 2~15자인 낱말의 목록입니다. 문장이 아니라 제품·서비스 이름을 넣으세요")
    words = words[:8]
    latest = _latest_business(db, when)
    rows = db.execute(select(BusinessSection).join(latest, BusinessSection.rcept_no == latest.c.rcept_no).where(
        or_(*[BusinessSection.text.contains(w, autoescape=True) for w in words]))).scalars().all()
    found: dict[int, dict] = {}
    for row in rows:
        entry = found.setdefault(row.company_id, {"score": 0, "matched": {}, "snippets": [], "rcept_no": row.rcept_no,
                                                  "bsns_year": row.bsns_year})
        weight = _SECTION_WEIGHT.get(row.section_no, 1)
        for word in words:
            count = row.text.count(word)
            if not count:
                continue
            entry["score"] += weight * min(count, 5)
            entry["matched"][word] = entry["matched"].get(word, 0) + count
            at = row.text.find(word)
            entry["snippets"].append((weight, {"section": row.title, "keyword": word,
                                               "text": " ".join(row.text[max(0, at - 90):at + 130].split())}))
    companies = {c.company_id: c for c in db.scalars(select(Company).where(Company.company_id.in_(list(found))))} if found else {}
    # 낱말이 많이 나온 것만 보면 그 말을 자주 쓰는 장비·부품 회사가 앞에 오고 정작 그것을 파는 회사가 밀린다.
    # 제품 표에서 그 낱말이 걸린 줄의 매출 비중을 점수에 더한다(비중 100%면 글에서 한 낱말이 낼 수 있는 가장 큰 점수만큼)
    shares = _product_shares(db, when, words) if found else {}
    for company_id, entry in found.items():
        entry["share"] = shares.get(company_id, 0.0)
        entry["score"] += entry["share"] * 0.6
    ranked = sorted(((i, e) for i, e in found.items()
                     if not listed_only or MARKETS.get(companies[i].corp_cls) in ("유가증권", "코스닥", "코넥스")),
                    key=lambda x: (-len(x[1]["matched"]), -x[1]["score"], companies[x[0]].label))
    out = []
    for company_id, entry in ranked[:30]:
        snippets, seen = [], set()
        for _, snippet in sorted(entry["snippets"], key=lambda s: -s[0]):
            if (snippet["section"], snippet["keyword"]) not in seen and len(snippets) < 3:
                seen.add((snippet["section"], snippet["keyword"]))
                snippets.append(snippet)
        out.append({**_brief(companies[company_id]), "matched": entry["matched"],
                    "product_share_pct": entry["share"] or None, "snippets": snippets,
                    "report": _report_name(db, entry["rcept_no"]), "rcept_no": entry["rcept_no"]})
    return {"as_of": when.isoformat(), "keywords": words, "total": len(ranked), "truncated": len(ranked) > 30, "companies": out,
            # 화면의 그래프가 쓴다. Agent에게는 보내지 않는다(이름이 _ 로 시작하는 값): 30곳 너머까지, 기업마다 많이 나온 낱말 둘
            "_graph": [(i, sorted(e["matched"].items(), key=lambda x: -x[1])[:2]) for i, e in ranked[:GRAPH_MENTIONS]],
            "note": "보고서 글에 낱말이 나온 회사입니다. matched 는 낱말별로 나온 횟수이고, 낱말이 나왔다고 그 사업이 주력이라는 뜻은 아닙니다. "
                    "product_share_pct 는 제품 표에서 이 낱말이 걸린 줄의 매출 비중(%)이고, 없으면 제품 표에서는 걸리지 않은 것입니다. "
                    "주력인지는 get_business 의 매출 비중 표로 확인하고, 확인하지 않은 회사는 '보고서에 언급이 있다'고만 말하세요"
                    if out else "이 낱말이 나온 사업보고서가 없습니다. 다른 이름(제품명, 원재료명)으로 다시 찾아 보세요"}


def get_business(db, company_id, as_of, sections=None) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    wanted = [int(n) for n in (sections or [1, 2])]
    latest = _latest_business(db, when, company.company_id)
    rows = db.execute(select(BusinessSection).join(latest, BusinessSection.rcept_no == latest.c.rcept_no)
                      .order_by(BusinessSection.section_no)).scalars().all()
    if not rows:
        return {"company": _brief(company), "sections": [],
                "note": "이 회사의 '사업의 내용'은 DB에 없습니다. 상장사의 2025 사업보고서와 2026 반기보고서만 담았습니다"}
    limit = 6000
    return {"company": _brief(company), "report": _report_name(db, rows[0].rcept_no), "rcept_no": rows[0].rcept_no,
            "disclosed_date": rows[0].disclosed_date.isoformat(),
            "available": [{"section": r.section_no, "title": r.title, "chars": len(r.text)} for r in rows],
            "sections": [{"section": r.section_no, "title": r.title, "text": r.text[:limit],
                          "cut": len(r.text) > limit or bool(r.truncated)} for r in rows if r.section_no in wanted],
            "note": "보고서에 적힌 글 그대로입니다. 연결 기준인지 별도 기준인지, 단위가 무엇인지는 표의 머리말을 따릅니다. "
                    "report 가 반기·분기보고서이면 매출액은 그 기간(반년·누적) 치이므로, 금액을 옮길 때 어느 보고서의 값인지 밝히세요"}


def _latest_products(db, when: date, company_id: int | None = None):
    """조회 시점까지 나온 보고서 가운데 회사마다 제품 표를 읽은 가장 나중 것의 접수번호."""
    latest = select(Product.company_id, func.max(Product.rcept_no).label("rcept_no")).where(Product.disclosed_date <= when)
    if company_id is not None:
        latest = latest.where(Product.company_id == company_id)
    return latest.group_by(Product.company_id).subquery()


# 보고서는 직접 만든 것을 "제품", 다른 회사가 만든 것을 사서 파는 것을 "상품"이라고 나눠 적는다
_GOODS = re.compile(r"^\s*상\s*품|[(\[]\s*상\s*품\s*[)\]]|상품\s*매출")
_MADE = re.compile(r"제\s*품|제\s*/?\s*상\s*품|제조|금융\s*상품|파생\s*상품")


def _resale(row: Product) -> bool:
    """그 줄이 받아서 파는 것인가. 표가 '상품'이라고 적었거나, 붙인 표준 이름이 모두 유통이다."""
    wording = [row.segment or "", row.name or ""]
    if any(_GOODS.search(text) for text in wording) and not any(_MADE.search(text) for text in wording):
        return True
    names = row.std_names or []
    return bool(names) and all(name.endswith(("유통", "도소매", "도매", "소매")) for name in names)


def _product_row(row: Product, codes: dict | None = None) -> dict:
    families = row.std_families or [None] * len(row.std_names or [])
    code = lambda name, family: (codes or {}).get((family, name))
    return {"segment": row.segment, "name": row.name, "share_pct": None if row.share_pct is None else float(row.share_pct),
            "products": [{"name": name, "family": family, **({"ksic": code(name, family)} if code(name, family) else {})}
                         for name, family in zip(row.std_names or [], families)],
            **({"unsure": True} if row.unsure else {}), **({"resale": True} if _resale(row) else {})}


_PRODUCT_NOTE = ("name 과 segment 는 보고서 표에 적힌 그대로, share_pct 는 그 줄이 매출에서 차지하는 비중(%)입니다. "
                 "share_pct 가 null 이면 보고서가 그 제품의 비중을 밝히지 않은 것이니 비중을 지어내지 말고 '비중은 공시에 없다'고 적으세요. "
                 "products 는 그 줄에 붙인 표준 이름과 제품군(그리고 한국표준산업분류 세세분류 코드 ksic)으로, 회사의 표 전체와 사업 개요를 보고 붙였지만 사람이 전부 확인한 것은 아닙니다. "
                 "답에는 표에 적힌 이름을 옮기세요. 한 줄에 여러 제품이 함께 적혀 있으면 share_pct 는 그 줄 전체의 비중이지 그 제품만의 비중이 아닙니다")


_squeeze = lambda text: re.sub(r"\s", "", text or "").lower()


def _product_shares(db, when: date, words: list[str]) -> dict[int, float]:
    """기업 → 제품 표에서 이 낱말들이 걸린 줄의 매출 비중 합(%). 표준 이름, 품목, 사업부문 어디에 걸려도 센다."""
    keys = [_squeeze(w) for w in words]
    latest = _latest_products(db, when)
    shares: dict[int, float] = {}
    for row in db.scalars(select(Product).join(latest, Product.rcept_no == latest.c.rcept_no)):
        if (row.share_pct or 0) > 0 and any(key in _squeeze(text) for key in keys for text in (*(row.std_names or ()), row.name, row.segment)):
            shares[row.company_id] = round(min(100.0, shares.get(row.company_id, 0.0) + float(row.share_pct)), 2)
    return shares


def find_by_product(db, as_of, families=None, keywords=None, ksic=None, min_share=None, listed_only: bool = True) -> dict:
    when = _date(as_of, "as_of", required=True)
    wanted = list(dict.fromkeys(str(f).strip() for f in (families or []) if str(f).strip()))
    unknown = [f for f in wanted if f not in FAMILY_FIELD]
    if unknown:
        raise ToolError(f"제품군 {unknown} 은 목록에 없습니다. 도구 설명의 제품군 목록에 있는 이름을 글자 그대로 쓰세요")
    words = list(dict.fromkeys(str(w).strip() for w in (keywords or []) if str(w).strip()))[:8]
    prefixes = [str(c).strip() for c in (ksic or []) if str(c).strip()]
    if any(not ksic_table.valid(c) or not c.isdigit() for c in prefixes):
        raise ToolError("ksic 는 한국표준산업분류 11차의 숫자 코드(2~5자리)입니다. 예: 26(전자부품), 261(반도체), 26111(메모리용 전자집적회로)")
    if not wanted and not words and not prefixes:
        raise ToolError("families, keywords, ksic 중 하나는 주어야 합니다")
    codes = {(c.family, c.name): c.ksic for c in db.scalars(select(ProductCode))}
    if any(not 2 <= len(w) <= 20 for w in words):
        raise ToolError("keywords 는 2~20자인 제품 이름의 목록입니다")
    keys = [_squeeze(w) for w in words]
    latest = _latest_products(db, when)
    found: dict[int, dict] = {}
    for row in db.scalars(select(Product).join(latest, Product.rcept_no == latest.c.rcept_no).order_by(Product.rcept_no, Product.row_no)):
        if row.share_pct is not None and row.share_pct <= 0:
            continue
        pairs = list(zip(row.std_names or [], row.std_families or [None] * len(row.std_names or [])))
        in_family = [(name, family) for name, family in pairs if family in wanted]
        by_word = [(name, family) for name, family in pairs if any(key in _squeeze(name) for key in keys)]
        raw_hit = any(key in _squeeze(row.name) or key in _squeeze(row.segment) for key in keys)
        if wanted and not in_family:
            continue
        if prefixes:
            pairs = [(name, family) for name, family in pairs if (codes.get((family, name)) or "").startswith(tuple(prefixes))]
            in_family = [pair for pair in in_family if pair in pairs]
            by_word = [pair for pair in by_word if pair in pairs]
            if not pairs:
                continue
        if keys and not by_word and not raw_hit:
            continue
        entry = found.setdefault(row.company_id, {"share_pct": 0.0, "line": 0.0, "rows": [], "rcept_no": row.rcept_no, "labels": [], "split": False, "made": False})
        entry["made"] = entry["made"] or not _resale(row)
        # 한 줄에 제품이 여럿이면 줄의 비중을 제품 수로 나눠, 찾는 제품의 몫만 더한다. 품목 칸의 글자로만 걸린 줄은 줄 전체를 센다
        hit = set(in_family if wanted else pairs) & set(by_word if keys and by_word else pairs)
        part = len(hit) / len(pairs) if pairs and hit else 1.0
        entry["split"] = entry["split"] or part < 1
        entry["share_pct"] = round(min(100.0, entry["share_pct"] + float(row.share_pct or 0) * part), 2)
        entry["line"] = round(min(100.0, entry["line"] + float(row.share_pct or 0)), 2)   # 나누지 않은, 그 줄 전체의 비중
        entry["rows"].append(_product_row(row, codes))
        # 그래프에 그릴 점: 제품군으로 찾았으면 그 제품군, 낱말로 찾았으면 걸린 제품 이름(없으면 찾은 낱말)
        if wanted and not keys:
            labels = [(family, "family") for _, family in in_family]
        elif keys:
            labels = [(name, "product") for name, _ in by_word] or [(words[0], "product")]
        else:   # 공식 분류 코드로만 찾았다
            labels = [(name, "product") for name, _ in pairs]
        for label in labels:
            if label not in entry["labels"]:
                entry["labels"].append(label)
    companies = {c.company_id: c for c in db.scalars(select(Company).where(Company.company_id.in_(list(found))))} if found else {}
    floor = float(min_share) if min_share is not None else 0.0
    ranked = sorted(((i, e) for i, e in found.items() if e["line"] >= floor
                     and (not listed_only or MARKETS.get(companies[i].corp_cls) in ("유가증권", "코스닥", "코넥스"))),
                    key=lambda x: (-x[1]["line"], -x[1]["share_pct"], companies[x[0]].label))
    spread: dict[str, set] = {}
    for company_id, entry in ranked:
        for row in entry["rows"]:
            for product in row["products"]:
                if product["family"]:
                    spread.setdefault(product["family"], set()).add(company_id)
    out = [{**_brief(companies[i]), "share_pct": e["share_pct"] or None,
            **({"share_is_estimate": True, "line_share_pct": e["line"]} if e["split"] and e["share_pct"] else {}),
            **({} if e["share_pct"] else {"share_not_disclosed": True}), **({} if e["made"] else {"resale_only": True}), "rows": e["rows"][:5],
            "report": _report_name(db, e["rcept_no"]), "rcept_no": e["rcept_no"]} for i, e in ranked[:LIMIT]]
    return {"as_of": when.isoformat(), "families": wanted, "keywords": words, "total": len(ranked), "truncated": len(ranked) > LIMIT,
            "companies": out,
            # 찾은 회사들이 어느 제품군에 걸쳐 있는지. 낱말로 찾았을 때 뜻이 다른 것이 섞였는지(예: 반도체와 반도체 장비) 가리는 데 쓴다
            "family_spread": sorted(({"family": f, "companies": len(ids)} for f, ids in spread.items()), key=lambda x: -x["companies"])[:15],
            # 화면의 그래프가 쓴다. Agent에게는 보내지 않는다: 찾은 회사 전부의 (기업, 매출 비중, 그래프에 그릴 점들)
            "_graph": [(i, e["share_pct"], e["labels"][:4]) for i, e in ranked],
            "fields": "resale_only 인 회사는 걸린 줄이 모두 '상품'(다른 회사가 만든 것을 사서 파는 것)이거나 유통입니다. 만드는 회사를 묻는 질문의 답에는 "
                      "넣지 말고, 물었을 때만 '받아서 파는 회사'로 따로 적으세요. 줄마다의 resale 도 같은 뜻입니다. "
                      "line_share_pct 가 있는 회사는 찾는 제품이 다른 제품과 한 줄에 함께 적힌 곳입니다. 그 제품만의 비중은 공시에 없고, "
                      "share_pct(줄의 비중을 제품 수로 나눈 어림값)와 line_share_pct(그 줄 전체의 비중) 사이 어딘가입니다. "
                      "답에는 '○○ 등이 함께 적힌 줄이 매출의 line_share_pct%'라고 줄 전체의 값으로 적으세요. 목록은 line_share_pct 가 큰 순서입니다. "
                      "share_not_disclosed 인 회사는 그 제품을 판다고 보고서에 적혀 있지만 비중은 밝히지 않은 곳입니다(share_pct 는 null). 목록의 뒤쪽에 옵니다. "
                      "회사의 share_pct 는 찾는 제품의 몫을 더한 값입니다. 표의 한 줄에 제품이 여럿 적혀 있으면 그 줄의 비중을 제품 수로 고르게 나눈 어림값이고 "
                      "(share_is_estimate), 이때는 답에 '약'을 붙이고 표에 적힌 줄의 이름과 비중을 함께 적으세요. " + _PRODUCT_NOTE,
            "note": "제품 표를 읽을 수 있었던 회사만 나옵니다. 표를 읽지 못한 회사, 제품 표가 없는 회사(금융업 등)는 빠져 있으니 "
                    "빠짐없이 찾아야 하면 search_business 로도 찾으세요" if out else
                    "조건에 맞는 회사가 없습니다. 다른 제품군이나 다른 표기(영문 약어, 상위 제품 이름)로 다시 찾거나 search_business 로 보고서 글에서 찾으세요"}


def get_products(db, company_id, as_of) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    latest = _latest_products(db, when, company.company_id)
    rows = db.scalars(select(Product).join(latest, Product.rcept_no == latest.c.rcept_no).order_by(Product.row_no)).all()
    if not rows:
        return {"company": _brief(company), "read": False, "products": [],
                "note": "이 회사의 제품 표는 읽지 못했거나 없습니다. 제품이 없다는 뜻이 아닙니다. get_business 로 '주요 제품 및 서비스'의 글을 직접 읽으세요"}
    codes = {(c.family, c.name): c.ksic for c in db.scalars(select(ProductCode))}
    return {"company": _brief(company), "read": True, "report": _report_name(db, rows[0].rcept_no), "rcept_no": rows[0].rcept_no,
            "disclosed_date": rows[0].disclosed_date.isoformat(), "total": len(rows),
            "products": [_product_row(row, codes) for row in rows],
            "fields": _PRODUCT_NOTE + ". ksic 는 그 제품에 붙인 한국표준산업분류(11차) 세세분류 코드입니다"}


def _after(url: str, when: date) -> bool:
    """기사 주소에 적힌 날짜(20260905 꼴)가 조회 시점보다 뒤인가. 기사의 발행일을 따로 저장하지 않아 주소로만 가린다."""
    for found in re.findall(r"(?<!\d)(20\d{2})[-/]?(\d{2})[-/]?(\d{2})(?!\d{3})", url):
        try:
            if date(*map(int, found)) > when:
                return True
        except ValueError:
            pass
    return False


def _move_group(group: dict, direction: str, when: date) -> dict:
    news = group.get("news") or {}
    if news.get("sources"):   # 조회 시점 뒤에 나온 기사는 그 시점에 볼 수 없던 것이다
        news = {**news, "sources": [s for s in news["sources"] if not _after(s["url"], when)]}
        if not news["sources"]:
            news = {}
    out = {"direction": direction, "strength": "strong" if group["grade"] == "뚜렷함" else "weak", "link_kind": group["kind"], "link": group["why"],
           "moved": group["n"], "of": group["of"], "streak_days": group.get("streak", 1),
           "members": [{"company_id": m["id"], "name": m["name"], "market": m.get("market"), "change_pct": m["change"]} for m in group["members"]]}
    if news.get("found") in ("group", "single"):
        out["articles"] = [{"title": s["title"], "url": s["url"]} for s in news["sources"]]
        out["article_scope"] = "이 종목들을 함께 다룬 기사" if news["found"] == "group" else "한 종목만 다룬 기사"
        out["auto_summary"] = news["reason"]
    elif news.get("found") == "none":
        out["articles"] = []
    return out


PERIODS = (("1주", 7), ("1개월", 30), ("3개월", 91))
_PEERS: dict[date, "hot.Links"] = {}   # 같은 제품을 파는 종목 무리. 날마다 한 번만 만든다 (무거운 조회라서)


def _peer_links(db, when: date):
    if when not in _PEERS:
        _PEERS.clear()
        _PEERS[when] = hot.load_links(db, when)
    return _PEERS[when]


def _closes(db, day: date, codes=None) -> dict[str, float]:
    rows = select(PriceDaily.stock_code, PriceDaily.close_price).where(PriceDaily.trade_date == day)
    if codes is not None:
        rows = rows.where(PriceDaily.stock_code.in_(list(codes)))
    return {code: float(close) for code, close in db.execute(rows) if close}


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


MONEY = ("revenue", "operating_income", "net_income", "total_assets", "total_liabilities", "total_equity",
         "current_assets", "current_liabilities")


def get_financials(db, company_id, as_of, basis=None) -> dict:
    """Agent에게 주는 값. 금액은 '89조 4,924억 원' 같은 글로 바꿔 준다. 모델이 억 단위를 원으로 옮기다 자릿수를 틀린 일이 있어서다(평가 7판 F20)."""
    from . import financials as fin
    found = financial_data(db, company_id, as_of, basis)
    unit = found["unit"]

    def words(item: dict) -> dict:
        return {k: (fin.readable(v, unit) if k in MONEY and isinstance(v, (int, float)) else v) for k, v in item.items()}
    balance = {k: words(v) for k, v in found["balance"].items()}
    found.pop("unit")
    return {**found, "quarters": [words(q) for q in found["quarters"]], "years": [words(y) for y in found["years"]],
            "balance": balance,
            "fields": "금액은 이미 조·억 원으로 적은 글이니 그대로 옮긴다. quarters 의 값은 그 석 달, years 는 그해 누적. "
                      "yoy_pct 는 같은 보고서가 적은 전년 같은 기간 값과 견준 증감률(%). rcept_no 는 그 값이 실린 보고서. "
                      "다음 분기 보고서는 분기 말에서 45일 안, 사업보고서는 90일 안에 나온다."}


def financial_data(db, company_id, as_of, basis=None) -> dict:
    """get_financials 의 숫자판. 금액은 unit(대개 억원) 단위의 수다. 카드(views.py)와 검산이 쓴다."""
    from . import financials as fin
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    chosen, by = fin.reports(db, company.company_id, when, basis)
    if not by:
        raise ToolError(f"{company.label} 의 정기보고서 주요계정이 이 DB에 없습니다(상장사만 모았고, {when} 까지 접수된 것만 봅니다)")
    newest = fin.latest(by)
    divisor, unit = fin.unit(newest.currency)
    ends = [r for r in by.values() if r.report == "annual" and r.period_end and r.period_end < (newest.period_end or when)]
    out = {"company": _brief(company), "basis": chosen, "basis_name": fin.BASIS_NAME[chosen], "unit": unit,
           "latest_report": f"{newest.fiscal_year} {fin.YTD_LABEL[newest.report].replace(' 누적', '')} ({newest.rcept_no[:4]}-{newest.rcept_no[4:6]}-{newest.rcept_no[6:8]} 접수)",
           "quarters": fin.quarters(by, divisor)[-8:], "years": fin.years(by, divisor),
           "balance": {"latest": fin.balance(newest, divisor),
                       **({"previous_year_end": fin.balance(max(ends, key=lambda r: r.period_end), divisor)} if ends else {})},
           "market_median": {"market": MARKETS.get(company.corp_cls, company.corp_cls), **fin.medians(db, when, company.corp_cls)},
           "fields": "금액은 unit 단위. quarters 의 값은 그 석 달, years 는 그해 누적. yoy_pct 는 같은 보고서가 적은 전년 같은 기간 값과 견준 것(나중에 다시 작성된 값이면 그 값). "
                     "rcept_no 는 그 값이 실린 보고서. 다음 분기 보고서는 분기 말에서 45일 안, 사업보고서는 90일 안에 나온다."}
    if chosen == "CFS" and basis != "CFS":
        out["basis_note"] = "연결 재무제표 기준입니다. 별도 기준은 basis=OFS 로 다시 부르세요"
    if newest.revenue is None and newest.revenue_ytd is None:
        out["revenue_note"] = "이 회사의 주요계정에는 매출액 줄이 없습니다(은행·보험 같은 금융회사는 영업수익을 따로 적지 않습니다)"
    return out


def get_major_holders(db, company_id, as_of, since=None) -> dict:
    from . import holdings
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    found = holdings.major_holders(db, company.company_id, when, _date(since, "since"))
    linked = {c.company_id: c for c in db.scalars(select(Company).where(Company.company_id.in_(
        {r["reporter_company_id"] for r in found["holders"] + found["recent_reports"] if r["reporter_company_id"]})))}
    for row in found["holders"] + found["recent_reports"]:
        linked_id = row.pop("reporter_company_id", None)
        if linked_id in linked:   # 보고자가 원장의 기업이면 그래프에 이을 수 있게 번호를 붙인다
            row["reporter_company"] = _brief(linked[linked_id])
    return {"company": _brief(company), **found, "note": holdings.OVERLAP_NOTE,
            **({"empty": f"{when} 까지 접수된 5% 보고가 없습니다(OpenDART 는 2024-10 부터의 보고를 줍니다)"} if not found["total_reports"] else {})}


def get_insider_trades(db, company_id, as_of, since=None, until=None) -> dict:
    from . import holdings
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    end = min(_date(until, "until") or when, when)
    start = _date(since, "since") or (end - timedelta(days=89))
    if start > end:
        raise ToolError("since 가 until 보다 늦습니다")
    found = holdings.insider_trades(db, company.company_id, start, end)
    return {"company": _brief(company), **found,
            "fields": "날짜는 보고 접수일(rcept_dt)이다. 증감이 0인 보고(unchanged_reports)는 늘린 쪽에도 줄인 쪽에도 넣지 않았다. "
                      "거래 단가와 금액, 실제 거래일, 장내·장외, 증여·상속 같은 사유는 이 자료에 없다."}


def _event_json(row, unit_name: str = "억원") -> dict:
    from . import capital, financials as fin
    out = {"date": row.report_date.isoformat(), "kind": row.kind, "kind_name": capital.LABELS.get(row.kind, row.kind), "rcept_no": [row.rcept_no]}
    if capital.is_cancel(row):
        out["kind_name"], out["treasury_cancel"] = "감자(자기주식 소각)", True
    if row.shares is not None:
        out["shares"] = row.shares
    if row.shares_before:
        out["shares_before"] = row.shares_before
        if row.shares and row.kind in ("rights_issue", "bonus_issue", "rights_bonus_issue"):
            out["new_shares_pct_of_before"] = round(row.shares / row.shares_before * 100, 2)
    if row.amount is not None:
        out["amount"] = fin.readable(row.amount / 1e8)
    if row.price is not None:
        out["price_won"] = float(row.price)
    if row.ratio_pct is not None:
        out["ratio_pct"] = float(row.ratio_pct)
    for key in ("method", "purpose"):
        if getattr(row, key):
            out[key] = getattr(row, key)
    if row.period_start or row.period_end:
        out["period"] = f"{row.period_start or ''}~{row.period_end or ''}"
    attrs = row.attrs or {}
    for key, name in (("bd_tm", "round"), ("bd_mtd", "maturity"), ("bd_intr_ex", "coupon_pct"), ("bd_intr_sf", "yield_to_maturity_pct"),
                      ("funds", "use_of_funds"), ("cr_std", "reduction_date"), ("nstk_lstprd", "listing_date")):
        if attrs.get(key):
            out[name] = {k: fin.readable(v / 1e8) for k, v in attrs[key].items()} if key == "funds" else attrs[key]
    return out


def get_capital_events(db, company_id, as_of, since=None, kinds=None) -> dict:
    from . import capital, financials as fin
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    start = _date(since, "since") or capital.SINCE
    rows = capital.events(db, company.company_id, start, when, kinds)
    by_kind = {}
    for row in rows:
        item = by_kind.setdefault(row.kind, {"kind_name": capital.LABELS.get(row.kind, row.kind), "count": 0, "shares": 0, "amount_won": 0})
        item["count"] += 1
        item["shares"] += row.shares or 0
        item["amount_won"] += row.amount or 0
    for item in by_kind.values():
        won = item.pop("amount_won")
        item["amount"] = fin.readable(won / 1e8) if won else None
    return {"company": _brief(company), "from": start.isoformat(), "to": when.isoformat(), "total": len(rows),
            "by_kind": by_kind, "events": [_event_json(r) for r in reversed(rows)][:40], "truncated": len(rows) > 40,
            "note": capital.NOT_HERE + " shares 는 사채의 경우 전환·행사·교환으로 생길 수 있는 주식 수다."}


def get_dividends(db, company_id, as_of) -> dict:
    from . import capital, financials as fin
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    years = capital.dividends(db, company.company_id, when)
    cancels = {}
    for row in capital.events(db, company.company_id, capital.SINCE, when, ["capital_reduction"]):
        if capital.is_cancel(row):
            item = cancels.setdefault(row.report_date.year, {"decisions": 0, "shares": 0, "rcept_no": []})
            item["decisions"] += 1
            item["shares"] += row.shares or 0
            item["rcept_no"].append(row.rcept_no)
    buys = {}
    for row in capital.events(db, company.company_id, capital.SINCE, when, ["buyback", "buyback_trust"]):
        item = buys.setdefault(row.report_date.year, {"decisions": 0, "amount_won": 0, "rcept_no": []})
        item["decisions"] += 1
        item["amount_won"] += row.amount or 0
        item["rcept_no"].append(row.rcept_no)
    num = lambda v: float(v) if v is not None else None
    out = {"company": _brief(company), "years": [{
        "fiscal_year": r.fiscal_year, "dps_common_won": num(r.dps_common), "dps_preferred_won": num(r.dps_preferred),
        "cash_total": fin.readable(r.cash_total / 1e8) if r.cash_total is not None else None,
        "payout_ratio_pct": num(r.payout_ratio), "payout_basis": fin.BASIS_NAME.get(r.basis or "", r.basis),
        "yield_common_pct": num(r.yield_common), "eps_won": num(r.eps), "rcept_no": [r.rcept_no]} for r in years],
        "buybacks": [{"year": y, "decisions": b["decisions"], "amount": fin.readable(b["amount_won"] / 1e8), "rcept_no": b["rcept_no"][:5]}
                     for y, b in sorted(buys.items())],
        "treasury_cancellations": [{"year": y, "decisions": c["decisions"], "shares": c["shares"], "rcept_no": c["rcept_no"][:5]} for y, c in sorted(cancels.items())],
        "fields": "years 는 2025 사업보고서의 당기·전기·전전기 값이다(그래서 rcept_no 가 같다). buybacks 는 자기주식 취득 결정과 신탁계약의 예정 금액 합계로, 실제 취득액이 아니다. "
                  "treasury_cancellations 는 감자결정으로 공시한 자기주식 소각이다(거래소에만 낸 소각 결정은 없다). 분기·중간배당의 회차별 금액, 앞으로의 배당은 없다."}
    if not years:
        out["empty"] = f"{when} 까지 접수된 사업보고서에 배당 표가 없습니다"
    return out


def get_price_history(db, company_id, as_of) -> dict:
    company, when = _company(db, company_id), _date(as_of, "as_of", required=True)
    if not company.stock_code:
        raise ToolError(f"{company.label} 은(는) 상장 종목이 아니어서 시세가 없습니다")
    days = list(db.scalars(select(PriceDaily.trade_date).where(PriceDaily.trade_date <= when, PriceDaily.trade_date >= when - timedelta(days=130))
                           .group_by(PriceDaily.trade_date).order_by(PriceDaily.trade_date.desc())))
    if not days:
        return {"company": _brief(company), "periods": [], "note": "그 시점 이전의 시세가 없습니다. 시세는 2026년부터 최근 약 90일만 보관합니다"}
    end = days[0]
    market = {c.stock_code: c.corp_cls for c in db.scalars(select(Company).where(Company.corp_cls == company.corp_cls, Company.stock_code.isnot(None)))}
    links = _peer_links(db, when)
    products = sorted(name for name, members in links.holders.items() if company.company_id in members)
    peer_ids = set().union(*(links.holders[name] for name in products)) - {company.company_id} if products else set()
    peer_codes = {c.stock_code: c.label for c in db.scalars(select(Company).where(Company.company_id.in_(peer_ids)))} if peer_ids else {}
    last = _closes(db, end)
    periods = []
    for label, span in PERIODS:
        start = next((d for d in days if d <= end - timedelta(days=span)), None)
        if start is None:
            periods.append({"period": label, "note": "보관한 시세가 이 기간을 다 덮지 못합니다"})
            continue
        first = _closes(db, start)
        change = lambda code: round((last[code] / first[code] - 1) * 100, 2) if code in last and code in first else None
        mine = change(company.stock_code)
        market_values = [v for v in (change(code) for code in market) if v is not None]
        peer_values = [v for v in (change(code) for code in peer_codes) if v is not None]
        periods.append({"period": label, "from": start.isoformat(), "to": end.isoformat(), "change_pct": mine,
                        "market_median_pct": _median(market_values), "market": MARKETS.get(company.corp_cls),
                        "peers_median_pct": _median(peer_values), "peers_counted": len(peer_values),
                        "vs_market_pp": None if mine is None or not market_values else round(mine - statistics.median(market_values), 2)})
    return {"company": _brief(company), "as_of": when.isoformat(), "last_trading_day": end.isoformat(), "periods": periods,
            "peers": {"basis": "같은 제품을 매출의 10% 이상으로 파는 상장사(그 제품을 가진 곳이 12곳 이하인 좁은 제품 이름만)",
                      "products": products[:6], "names": sorted(peer_codes.values())[:12], "total": len(peer_codes)},
            "fields": "change_pct 는 그 기간의 종가 기준 수익률(%), market_median_pct 는 같은 시장 상장 종목 수익률의 중앙값, "
                      "peers_median_pct 는 같은 제품을 파는 종목들의 중앙값, vs_market_pp 는 시장 중앙값과의 차이(%p). "
                      "수익률은 지난 사실이며 원인을 말해 주지 않는다. 앞으로의 주가에 대해서는 말하지 않는다. "
                      "출처는 한국거래소(KRX) 통계정보의 일별 시세를 가공한 값이다"}


def get_price_moves(db, as_of, day=None, company_id=None, keyword=None, direction: str = "both", lookback_days: int = 30) -> dict:
    when = _date(as_of, "as_of", required=True)
    if direction not in ("up", "down", "both"):
        raise ToolError("direction 은 up, down, both 중 하나입니다")
    sides = lambda payload: [(d, side) for d, side in (("up", payload), ("down", payload.get("down") or {})) if direction in (d, "both")]
    note = ("종목군은 그날 시장 중앙값보다 3%p 이상 움직이고 그날 상·하위 8%에 든 종목 가운데, 연결 고리(link)를 함께 가진 종목이 셋 이상인 것입니다. "
            "link 는 종목들의 공통점이지 주가가 움직인 원인이 아닙니다. 원인을 말하려면 articles 의 기사 제목을 근거로 대고 주소를 함께 적으세요. "
            "auto_summary 는 모델이 기사를 요약한 것으로 기사와 어긋날 수 있으니 '자동 요약에 따르면'이라고 밝혀 쓰고, articles 가 없으면 원인을 짐작하지 마세요. "
            "strength 가 weak 인 것은 넷에 하나꼴로 우연히도 나옵니다. moved/of 는 움직인 종목 수와 그 연결 고리를 가진 상장사 수입니다. "
            "change_pct 는 전 거래일 종가 대비 등락률(%)이며, 앞으로의 주가에 대해서는 말하지 않습니다.")
    if company_id is None and not keyword:
        target = _date(day, "day") or when
        row = db.scalar(select(HotDay).where(HotDay.trade_date <= min(target, when)).order_by(HotDay.trade_date.desc()).limit(1))
        if row is None:
            return {"days": [], "note": "그 시점 이전에 계산해 둔 거래일이 없습니다. 주가는 2026년 2월부터 있습니다"}
        payload, groups, alone = row.payload, [], []
        asked = _date(day, "day")
        moved_day = (None if asked is None or asked == row.trade_date else
                     f"{asked.isoformat()} 은 조회 시점({when.isoformat()})보다 뒤여서 볼 수 없습니다. 아래는 {row.trade_date.isoformat()} 의 값입니다" if asked > when else
                     f"{asked.isoformat()} 은 거래일이 아니거나(휴장일·주말) 계산해 둔 자료가 없습니다. 아래는 그 앞의 가장 가까운 거래일 {row.trade_date.isoformat()} 의 값이니, 답에서 날짜가 다르다는 것을 먼저 밝히세요")
        for d, side in sides(payload):
            groups += [_move_group(g, d, when) for g in side.get("groups", [])]
            alone += [{"direction": d, "company_id": a["id"], "name": a["name"], "market": a.get("market"), "change_pct": a["change"],
                       "recent_filings": [{"title": f["title"], "rcept_no": f["rcept_no"], "date": f["date"]} for f in a.get("filings", [])]}
                      for a in side.get("alone", [])[:10]]
        groups.sort(key=lambda g: (g["strength"] != "strong", -abs(sum(m["change_pct"] for m in g["members"]) / len(g["members"]))))
        return {"day": row.trade_date.isoformat(), **({"day_note": moved_day} if moved_day else {}),
                "market_median_pct": payload["market"], "watched": payload["watched"],
                "groups": groups, "alone": alone, "alone_note": "어디에도 묶이지 않고 시장 대비 10%p 이상 움직인 종목 가운데 많이 움직인 10곳씩",
                "fields": note}
    company = _company(db, company_id) if company_id is not None else None
    word = _squeeze(keyword) if keyword else None
    if word is not None and not 2 <= len(word) <= 20:
        raise ToolError("keyword 는 2~20자입니다")
    start = when - timedelta(days=max(1, min(int(lookback_days or 30), 120)))
    days = []
    for row in db.scalars(select(HotDay).where(HotDay.trade_date <= when, HotDay.trade_date >= start).order_by(HotDay.trade_date.desc())):
        hits, solo = [], []
        for d, side in sides(row.payload):
            for g in side.get("groups", []):
                by_company = company is not None and any(m["id"] == company.company_id for m in g["members"])
                by_word = word is not None and (any(word in _squeeze(w) for w in g["why"]) or any(word in _squeeze(m["name"]) for m in g["members"]))
                if by_company or by_word:
                    hits.append(_move_group(g, d, when))
            if company is not None:
                solo += [{"direction": d, "change_pct": a["change"], "recent_filings": a.get("filings", [])}
                         for a in side.get("alone", []) if a["id"] == company.company_id]
        if hits or solo:
            days.append({"day": row.trade_date.isoformat(), "market_median_pct": row.payload["market"], "groups": hits,
                         **({"moved_alone": solo} if solo else {})})
    return {"from": start.isoformat(), "to": when.isoformat(), **({"company": _brief(company)} if company else {}), **({"keyword": keyword} if keyword else {}),
            "total_days": len(days), "days": days[:15], "truncated": len(days) > 15,
            "fields": note + " moved_alone 은 그 회사가 종목군에 묶이지 않고 혼자 크게 움직인 날입니다(많이 움직인 20곳 안에 든 날만 기록됩니다)."}


def show_view(db, kind, company_id, as_of, other_company_id=None, since=None) -> dict:
    import sys
    from . import views
    view = views.build(db, sys.modules[__name__], kind, company_id, as_of, other_company_id, since)
    return {"shown": view["title"], "summary": view.get("summary"), "notes": view.get("notes"), "_view": view}


def show_on_graph(db, highlight, also=None, views=None, as_of=None, company_id=None) -> dict:
    """화면에 보일 기업을 받아 확인만 한다. 실제로 화면을 움직이는 것은 이 호출을 본 서버와 브라우저다 (api._Found, Chat.tsx).
    views 가 있으면 카드의 값을 여기서 채워 _views 로 돌려준다. 카드 하나가 실패해도 그래프 표시는 그대로 한다.
    as_of, company_id 는 스키마에 없지만 모델이 카드의 값을 밖에 적는 일이 잦아서, 카드에 빠진 값의 기본으로 받는다."""
    wanted = [int(i) for i in dict.fromkeys(highlight or [])][:12]
    extra = [int(i) for i in dict.fromkeys(also or []) if int(i) not in wanted][:40]
    if not wanted:
        raise ToolError("highlight 에 강조할 기업의 company_id 를 하나 이상 주세요")
    known = {c.company_id: c for c in db.scalars(select(Company).where(Company.company_id.in_(wanted + extra)))}
    missing = [i for i in wanted + extra if i not in known]
    if missing:
        raise ToolError(f"company_id {missing} 는 없습니다. find_company 나 앞선 조회 결과의 company_id 를 쓰세요")
    out = {"shown": True, "highlight": [_brief(known[i]) for i in wanted], "also": len(extra),
           "note": "화면에 표시했습니다. 이어서 답을 쓰세요. 화면에 표시했다는 말은 답에 적지 않아도 됩니다"}
    built, cards = [], []
    for spec in (views or [])[:2]:
        spec = {"as_of": as_of, "company_id": company_id, **{k: v for k, v in dict(spec).items() if v is not None}}
        card = call(db, "show_view", {k: v for k, v in spec.items() if k in ("kind", "company_id", "as_of", "other_company_id", "since") and v is not None})
        if "error" in card:
            cards.append({"kind": spec.get("kind"), "error": card["error"]})
        else:
            built.append(card["_view"])
            cards.append({k: card[k] for k in ("shown", "summary", "notes") if card.get(k)})
    if cards:
        out["views"] = cards
        out["note"] += ". 카드에 나온 값을 답에서 다시 표로 늘어놓지 말고 요점과 해석만 적으세요"
    if built:
        out["_views"] = built
    return out


def tools() -> list[dict]:
    """Agent에게 줄 도구 목록."""
    import os
    return [t for t in TOOLS if (t["name"] != SCREEN_TOOL or os.environ.get("ASK_SCREEN_TOOL", "1") != "0")
            and (t["name"] not in COMPOSITE_TOOLS or os.environ.get("ASK_COMPOSITE", "0") == "1")]


FUNCTIONS = {"get_capital_events": get_capital_events, "get_dividends": get_dividends, "get_major_holders": get_major_holders, "get_insider_trades": get_insider_trades, "get_financials": get_financials, "show_view": show_view, "get_price_history": get_price_history, "compare_reports": compare_reports, "holder_changes": holder_changes, SCREEN_TOOL: show_on_graph, "get_price_moves": get_price_moves, "find_by_product": find_by_product, "get_products": get_products,
             "search_business": search_business, "get_business": get_business, "find_company": find_company, "get_relations": get_relations, "get_filings": get_filings,
             "list_companies": list_companies, "find_disclosers": find_disclosers,
             "find_paths": find_paths, "get_coverage": get_coverage}


def call(db, name: str, arguments: dict) -> dict:
    """도구 하나를 부른다. Agent가 고칠 수 있는 잘못은 예외 대신 error 로 돌려준다."""
    if name not in FUNCTIONS:
        return {"error": f"{name} 이라는 도구는 없습니다"}
    try:
        return FUNCTIONS[name](db, **arguments)
    except ToolError as error:
        return {"error": str(error)}
    except TypeError as error:
        return {"error": f"인자가 맞지 않습니다: {error}"}
