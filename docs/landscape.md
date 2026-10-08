# 남들은 어떻게 하나 — 문헌·사례 조사

2026-10-05 조사. 주제를 여섯 갈래로 나눠 찾았고, 건마다 출처 페이지를 열어 확인했다.
숫자는 출처 페이지를 요약 모델로 읽은 값이다. **인용하기 전에 원문 표에서 다시 대조한다.**
"우리"는 이 프로젝트(공시 기반 기업 관계 그래프)다. 설계에 반영할 내용은 맨 아래 "설계 수정 제안"에 모았다.

먼저 본 다섯 건: Microsoft GraphRAG, LinkedIn 지식 그래프 RAG(SIGIR 2024, 처리 시간 중앙값 28.6% 단축),
Uber QueryGPT(쿼리 작성 약 10분 → 3분), Zep Graphiti(이중 시간 모델), Morgan Stanley 사내 도우미(평가 먼저, 사용률 98%).

---

## 1. Graph RAG와 LLM 기반 지식 그래프 구축 (17건)

### 그래프 방식이 언제 나은가를 따진 연구

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [GraphRAG-Bench](https://arxiv.org/abs/2506.05690) | 2025, arXiv | 질문을 난이도 4단계로 나눠 비교. 사실 찾기는 일반 RAG가 앞서고(소설 60.92% 대 49.29%), 복잡한 추론은 그래프가 앞선다(53.38% 대 42.93%). 질의당 토큰은 RAG 약 879, MS-GraphRAG(global) 약 331,375 | 문항을 유형별로 나눠 보고한다. 질의당 토큰도 같이 낸다 |
| [RAG vs. GraphRAG](https://arxiv.org/abs/2502.11371) | 2025, arXiv | 조건을 통일해 비교. 단일 단계는 RAG 우세, 다단계는 그래프 우세. **시간 질문에서 차이가 가장 크다**(49.91~53.34% 대 30.70%). **LLM이 만든 그래프에는 정답 대상의 약 65%만 들어 있다** | 규칙 우선 추출의 근거. LLM 추출분은 재현율을 따로 잰다. 그래프 조회와 원문 검색을 함께 준다 |
| [편향 없는 GraphRAG 평가](https://arxiv.org/abs/2506.06331) | 2025, arXiv | LLM이 두 답 중 승자를 고르는 평가는 순서만 바꿔도 승률이 30% 넘게 달라진다. LightRAG의 승률 66.70%가 편향 제거 후 39.06% | LLM 심판의 쌍대 비교를 대표 지표로 쓰지 않는다. 정답 대조를 유지한다 |
| [HippoRAG 2](https://arxiv.org/abs/2502.14802) | ICML 2025 | 단순 QA에서 임베딩만 쓴 검색(NQ F1 61.9)이 GraphRAG(46.9), LightRAG(16.6)보다 낫다고 보고 | 단순 사실 질문에서 그래프가 이긴다고 주장하지 않는다 |
| [LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) | 2024, Microsoft | 미리 요약하지 않고 질의 때만 LLM을 쓴다. 색인 비용이 전체 GraphRAG의 0.1% | 그룹 요약을 미리 만들지 않는다. Agent의 도구 호출 수에 예산을 둔다 |

### 방법

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [LightRAG](https://arxiv.org/abs/2410.05779) | 2024, arXiv | 대상·관계마다 검색 키와 요약을 붙인다. 증분 갱신 | 독립 평가에서 성능이 불안정. 도입하지 않는다 |
| [HippoRAG](https://arxiv.org/abs/2405.14831) | NeurIPS 2024 | 그래프에서 한 번에 연결을 찾는다. 반복 검색보다 10~30배 싸고 6~13배 빠르다고 보고 | "반복 검색 대비 비용·시간" 비교 틀 |
| [RAPTOR](https://arxiv.org/abs/2401.18059) | 2024 | 글을 묶어 요약하는 나무 구조 | 관계 조회가 핵심인 우리와 거리가 멀다 |
| [Think-on-Graph](https://arxiv.org/abs/2307.07697) | ICLR 2024 | LLM이 그래프 위에서 다음에 볼 관계와 이웃을 고르며 경로를 찾는다. 경로가 근거로 남는다 | 답에 탐색 경로를 붙인다. 이웃이 많은 기업에는 관계 종류 필터와 상위 N개 |
| [GraphReader](https://arxiv.org/abs/2406.14550) | EMNLP 2024 | 정해진 함수(노드 읽기, 이웃 읽기)와 계획·메모 | Agent에 계획 → 조회 → 메모 → 충분한지 판단 절차 |
| [G-Retriever](https://arxiv.org/abs/2402.07630) | 2024 | 질문에 맞는 작은 연결 부분그래프를 찾는다 | 화면의 "고른 기업 N개를 잇는 최소 그림" |
| [GNN-RAG](https://arxiv.org/abs/2405.20139) | 2024 | 최단 경로를 문장으로 풀어 LLM에 준다 | 경로 도구의 출력을 한 줄 문장으로 |
| [StructRAG](https://arxiv.org/abs/2410.08815) | 2024 | 질문 유형에 맞는 구조(표·그래프·목록)를 고른다 | 조회 결과를 유형별 형식으로 넘긴다 |
| [Graph RAG 서베이](https://arxiv.org/abs/2408.08921) | 2024 | 색인 → 검색 → 생성 3단계로 정리 | 우리 구조를 설명하는 틀 |

### 그래프 만들기

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [KGGen](https://arxiv.org/abs/2502.09956) | 2025, arXiv | 추출 → 합치기 → 같은 대상 묶기. 가장 좋은 추출기도 원문 정보의 66~73%만 보존 | LLM 추출 뒤 이름 묶기 단계. 사람이 확인한 사실 N개 중 되찾은 비율로 재현율을 잰다 |
| [FinReflectKG](https://arxiv.org/abs/2508.17906) | ICAIF 2025 | S&P 100의 10-K에서 공급·지분 관계를 LLM으로 추출. 추출 → 비평 → 수정 반복. 가장 좋은 방식도 규칙 통과율 64.8%, 정밀도 39.1% | **가장 가까운 사례.** 주요 고객 추출에 비평·수정 반복과 자동 규칙 검사. 우리 규칙 추출 97%와 나란히 놓으면 설계 근거 |
| [LLM Graph Transformer](https://www.langchain.com/blog/enhancing-rag-based-applications-accuracy-by-constructing-and-leveraging-knowledge-graphs), [PropertyGraphIndex](https://www.llamaindex.ai/blog/introducing-the-property-graph-index-a-powerful-new-way-to-build-knowledge-graphs-with-llms) | 2024, Neo4j·LlamaIndex | 구조화 출력으로 추출. 미리 짠 질의에 LLM이 인자만 채우는 검색기(CypherTemplateRetriever)가 있다 | 우리 "조회 함수만 부른다"와 같은 설계 |

### 이 갈래의 시사점

1. **그래프가 항상 낫지 않다.** 단순 사실 질문은 일반 검색과 비슷하거나 진다. 이기는 곳은 다단계와 시간 질문이다
2. **LLM이 만든 그래프는 정답의 3분의 1쯤을 놓친다.** 규칙 우선 추출이 맞다. LLM 추출분은 등급을 나누고 재현율을 따로 잰다
3. **이 분야의 "승률"은 평가 방식 때문에 부풀려진 경우가 많다.** 정답 대조 평가를 유지한다
4. **미리 요약·색인하는 비용은 대부분 불필요하다.** 질의 때 도구로 탐색한다
5. **정해진 조회 함수로 그래프를 걸어 다니는 설계는 검증된 계보에 있다.** 경로를 근거로 남기는 것이 핵심이다.
   조사한 17건 중 이중 시간 모델을 다룬 것은 없었다

---

## 2. 시간이 붙은 지식 그래프, 시점 조회, Agent 기억, 미래 정보 누수 (19건)

### 시간 질문

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [Test of Time](https://arxiv.org/abs/2406.09170) | 2024, arXiv | 가짜 이름의 합성 그래프로 시간 추론을 잰다. 사실 하나를 읽는 질문은 GPT-4 89.43%, 여러 사실을 줄 세우는 질문은 28.29%, 기간 계산은 16.00%. **사실을 나열하는 순서만 바꿔도 73.57% → 45.71%** | 조회 결과를 대상·시작일 순으로 정렬해 준다. 기간 계산과 순서 정렬은 코드가 한다. 일부 문항을 가명 회사로 만든다 |
| [TempReason](https://arxiv.org/abs/2306.08952) | ACL 2023 | "그 시점에 무엇이었나" 질문에서 문서를 주면 8.5, **정리된 사실을 주면 47.5** | 조회 계층을 두는 근거 |
| [시간 지식 그래프 서베이](https://arxiv.org/abs/2403.04782), [시간 그래프 질의응답 서베이](https://arxiv.org/abs/2406.14191) | 2024 | (주체, 관계, 상대, 시작, 끝) 구조. 질문 유형 분류: 명시적 시점, 암시적 시점, 순서, 전후 비교, 기간 | 기준일이 한 점인지 구간인지 점검. 질문 유형 점검표 |
| [MRAG / TempRAGEval](https://arxiv.org/abs/2412.15540) | 2024 | 질문의 시점만 바꾸면 기존 검색기의 정답 재현율이 85.8% → 54.7%. 시간 조건을 의미 검색과 분리해 규칙으로 처리 | 도구 인자에 시점을 필수로. "같은 질문, 시점만 다른" 문항 쌍 |

### 이중 시간 모델의 표준과 실무

| 이름 | 핵심 | 우리에게 |
|---|---|---|
| [SQL:2011 시간 기능](https://sigmodrecord.org/2012/09/30/temporal-features-in-sql2011/) | 적용 시간 표, 시스템 버전 표, 이중 시간 표 | 표준 용어. 표준의 시스템 시간은 "DB에 들어온 시각"이라 우리 공개일과 다른 축이다 |
| [MariaDB 시스템 버전 표](https://mariadb.com/docs/server/reference/sql-structure/temporal-tables/system-versioned-tables) | MariaDB는 내장 지원, MySQL은 없음 | MySQL에서는 칸을 직접 관리하는 것이 맞다. 이력 줄은 추가만 하고 고치지 않는다 |
| [XTDB](https://docs.xtdb.com/concepts/key-concepts.html) | 모든 줄에 유효 시작·끝, 시스템 시작·끝 네 칸 | 우리에게 "관계가 끝난 날"과 "우리 DB에 들어온 시각"이 있는지 점검 |
| [DuckDB ASOF JOIN](https://duckdb.org/docs/current/guides/sql_features/asof_join.html) | "그 시각 이전의 가장 최근 값"으로 붙이는 조인 | 우리의 "가장 최근 사업보고서" 규칙이 이것이다. 경계(같은 날 포함 여부)와 짝 없는 회사의 처리를 명문화 |
| [Feast point-in-time join](https://docs.feast.dev/getting-started/concepts/point-in-time-joins) | 과거로만 훑어 붙인다. 최대 유효 기한(TTL)을 둔다. 사건 시각과 기록 시각을 구분 | **유효 기한.** 보고서를 더 내지 않는 회사의 마지막 표가 영원히 현재로 남지 않게 한다 |

### Agent 기억

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [MemGPT](https://arxiv.org/abs/2310.08560) | 2023 | LLM이 함수 호출로 외부 기억을 넣고 꺼낸다 | 사실 저장소를 LLM이 고치게 하지 않는다 |
| [Mem0 / Mem0g](https://arxiv.org/abs/2504.19413) | 2025 | 낡은 관계를 지우지 않고 무효 표시. 시간 질문 점수 55.51 → 58.13 | 무효일 방식의 근거 |
| [A-MEM](https://arxiv.org/abs/2502.12110) | NeurIPS 2025 | 기존 기억을 덮어쓰며 "진화" | 반면교사. 과거를 재현할 수 없다 |
| [LoCoMo](https://arxiv.org/abs/2402.17753) | 2024 | 시간 추론이 가장 어렵다(사람 92.6, 최고 모델 25.0). 답이 없는 함정 질문 범주 | "그 시점에는 아직 공시되지 않은 관계"를 묻는 문항 |
| [LongMemEval](https://arxiv.org/abs/2410.10813) | ICLR 2025 | 다섯 능력: 추출, 여러 세션, 시간, **지식 갱신, 기권** | 평가 범주에 갱신(정정 전후로 답이 다름)과 기권을 넣는다 |

### 금융에서의 미래 정보 누수

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [Glasserman & Lin](https://arxiv.org/abs/2309.17322) | 2023 | 회사 이름이 보이면 LLM이 글이 아니라 자기 지식으로 답한다 | 주요 고객 추출을 가명 판과 비교하는 표본 검사 |
| [Sarkar & Vafa](https://icml.cc/virtual/2025/48685) | 2024 | "주어진 정보로는 알 수 없어야 하는 것"을 맞히면 누수. 프롬프트로는 못 막는다 | 본문에 없는 고객사가 추출되면 누수 |
| [The Memorization Problem](https://arxiv.org/abs/2504.14765) | 2025 | "2010년 이전 자료만 써라"고 해도 그 뒤 사실을 98.0% 맞힌다. 이름을 가려도 대기업은 85% 넘게 알아본다 | **기준선 문항을 모델의 지식 마감 전·후로 나눠 집계한다** |
| [ChronoBERT](https://arxiv.org/abs/2502.21206), [Time Machine GPT](https://arxiv.org/abs/2404.18543), [Look-Ahead-Bench](https://arxiv.org/abs/2601.13770) | 2024~2026 | 시점까지의 글만으로 학습한 모델. "마감 이후 사실을 맞히면 누수"로 검증 | 회귀 테스트: 어떤 시점으로 조회한 결과에 그 뒤 공개된 줄이 한 건이라도 있으면 실패 |
| [금융 LLM 평가의 다섯 편향](https://arxiv.org/abs/2602.14233) | 2026 | 미래 정보, 생존, 서사, 목적, 비용. 논문 164편 중 어느 편향도 28% 넘게 다뤄지지 않았다 | 평가 보고서의 "한계" 절 구성. **생존 편향: 지금의 회사 목록에서 출발하면 사라진 회사가 과거 그래프에서 빠진다** |

### 이 갈래의 시사점

1. **우리 DB에 들어온 시각이 없다.** 기준일·공개일·무효일은 모두 세상 쪽 시간이다. 추출 규칙이나 프롬프트를 고쳐 다시 넣으면
   "지난달 보여 준 답"을 되살릴 수 없다. 적재 시각이나 추출 버전 칸이 필요하다
2. **관계의 끝과 유효 기한이 없다.** 보고서를 더 내지 않는 회사의 마지막 표가 영원히 현재로 남는다.
   "없음"과 "모름"을 Agent가 구분하게 해야 한다
3. **기준선에 누수 경로가 있다.** 모델이 학습 때 외운 사실은 검색 없이 맞힌다. 과거 시점 질문에 웹 검색은 최신 정보를 준다.
   정답지가 "그 시점의 정답"인지 "지금의 정답"인지 문항마다 밝혀야 한다
4. **LLM 추출이 글에 없는 관계를 기억으로 채울 수 있다.** 공개일은 옛날로 찍히는데 내용은 미래 정보가 된다. 시점 필터로는 못 잡는다
5. **시간 계산은 코드가 하고 LLM은 정렬된 사실만 읽는다**

---

## 3. Text-to-SQL과 그 대안인 의미 계층·도구 설계 (24건)

**의미 계층**은 원본 표 위에 지표와 규칙을 사람이 미리 정의해 둔 층이다. LLM은 표 대신 이 정의를 고른다. 우리 조회 계층과 같은 자리다.

### 벤치마크

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [Spider 2.0](https://arxiv.org/abs/2411.07763) | ICLR 2025 | 실제 기업 작업 632개. 2024년 논문 때 최고 21.3%(같은 모델이 옛 벤치마크에서는 91.2%). 옛 기법(DIN-SQL, DAIL-SQL, CHESS)은 0~2%. 2026년 순위표 상위는 96%대(독립 검증 여부 불명) | "학술 벤치마크 점수가 높다"를 도입 근거로 쓰지 않는다 |
| [BIRD](https://bird-bench.github.io/) | NeurIPS 2023~ | 사람 92.96%, 1위 82.95%(2026-09) | 원본 표 SQL의 현실적 상한 |
| [LiveSQLBench](https://livesqlbench.ai/) | 2025~ | 업무 지식 문서를 읽고 SQL에 반영해야 한다. 상위 48.00% | 규칙을 글로 주고 LLM이 매번 반영하게 하는 방식은 아직 불안정 |
| [BIRD-INTERACT](https://arxiv.org/abs/2510.05318) | ICLR 2026 | 모호한 질문을 되묻는 능력. GPT-5가 8.67%, 17.00% | 모호한 질문 문항을 넣고 되묻는지 본다 |
| [Spider2-V](https://arxiv.org/abs/2407.10956) | 2024 | 화면 조작이 섞인 데이터 작업. 14.0% | 관련 없음 |

### 방법

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [DIN-SQL](https://arxiv.org/abs/2304.11015) | NeurIPS 2023 | 문제를 작은 단계로 쪼갠다 | 기업 찾기 → 조회의 2단계 순서를 도구 설명에 적는다 |
| [DAIL-SQL](https://arxiv.org/abs/2308.15363) | 2023 | 비슷한 예시를 골라 넣는다 | SQL 도구를 더할 때만 |
| [MAC-SQL](https://arxiv.org/abs/2312.11242) | COLING 2025 | 실행 오류를 보고 고치는 루프 | 조회 함수의 오류 메시지를 "무엇을 고쳐 다시 부르라"로 쓴다 |
| [CHESS](https://arxiv.org/abs/2405.16755) | 2024 | 질문 속 단어를 DB의 실제 값과 맞추는 단계 | 기업 찾기를 독립 도구로 두는 설계가 타당 |
| [CHASE-SQL](https://arxiv.org/abs/2410.01943) | 2024 | 후보 여러 개 생성 후 선택 | 비용이 커서 맞지 않는다 |
| [ReFoRCE](https://arxiv.org/abs/2502.00675) | 2025 | 같은 방법이 모델만 바꿔 35.83 → 62.89 | 방법보다 모델 세대가 점수를 움직인다. 평가를 모델별로 다시 돌릴 수 있게 고정 |

### 기업 사례

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [LinkedIn SQL Bot](https://www.linkedin.com/blog/engineering/ai/practical-text-to-sql-for-data-analytics) | 2024 | 평소 쓰던 화면에 넣자 채택이 5~10배. 평가 130문항 중 약 60%는 정답이 여러 개. 사용자가 SQL을 고쳐 쓴다 | 정답이 여러 개인 문항을 표시. 그래프 화면 안에 Agent를 넣는 근거 |
| [Pinterest](https://medium.com/pinterest-engineering/how-we-built-text-to-sql-at-pinterest-30bad30dabff) (원문 미확인) | 2024 | 2차 출처 기준 첫 시도 수락률 20% → 40%대 | 사람이 SQL을 검토하지 않으면 이 수준이 곧 오답률 |
| [Salesforce Horizon Agent](https://www.salesforce.com/blog/text-to-sql-agent/) | 2025 | 출시 초기 정확도 약 50%. 지식을 고칠 때마다 회귀 테스트 | 규칙을 바꿀 때마다 문항을 자동으로 다시 돌린다 |
| [Vercel d0](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools) | 2025 | **반대 사례.** 전용 도구를 없애고 bash 하나만 남겼더니 성공률 80% → 100%(질의 5개). 단, 잘 정리된 의미 계층이 있어서 가능했다고 저자가 밝힘 | 없앤 것은 "모델의 추론을 대신하는 도구"였다. 우리 함수는 데이터 규칙이라 남기는 쪽. 함수가 자잘하게 늘어나는 것은 경계 |
| [OpenAI 사내 데이터 Agent](https://openai.com/index/inside-our-in-house-data-agent/) (원문 미확인) | 2026 | 표를 만드는 코드를 읽혀 뜻을 파악 | SQL 도구를 더한다면 조회 계층의 주석을 맥락으로 |

### 의미 계층

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [Snowflake Cortex Analyst](https://www.snowflake.com/en/engineering-blog/cortex-analyst-text-to-sql-accuracy-bi/) | 2024 | 원본 스키마 대신 정리된 모델을 준다. 자체 평가 150문항에서 90%+ 대 51% | 규칙에 이름을 붙인다. 답에 "어떤 규칙이 적용됐는지" 표시 |
| [Databricks Genie](https://www.databricks.com/blog/how-build-production-ready-genie-spaces-and-build-trust-along-way) | 2026 | 평가 질문을 먼저 정하고 한 단계씩 맥락을 더한다. 13문항 0% → 100% | 문항 점수를 "무엇을 더했을 때 몇 점 올랐나" 단계별로 남긴다 |
| [dbt 의미 계층 대 Text-to-SQL, 2026 재실험](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026) | 2026 | **세 선택지를 직접 비교한 유일한 자료.** 원본에 가까운 표에 SQL 64.5%, 정리된 모델 위 SQL 84~90%, 의미 계층 98~100%. 의미 계층은 못 하면 못 한다고 하고, SQL은 "기분 좋게 틀린 숫자를 준다". 판매사 자체 실험, 11문항 × 20회 | 의미 계층 먼저, 범위 밖 질문만 SQL로. 같은 질문을 반복해 일관성도 잰다 |
| [Looker](https://cloud.google.com/blog/products/business-intelligence/how-lookers-semantic-layer-enhances-gen-ai-trustworthiness) | 2025 | 오류 최대 3분의 2 감소 주장(세부 미공개) | 근거로는 약하다 |
| [data.world 지식 그래프 벤치마크](https://arxiv.org/abs/2311.07509) | 2023 | SQL DB에 직접 16%, 지식 그래프 표현을 거치면 54% | 질문 난이도 × 스키마 난이도로 나눈 평가 |

### 도구 설계와 그래프 질의

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [Anthropic, Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | 2025 | API를 하나씩 감싸지 말고 작업 흐름에 맞춘 소수의 도구. 식별자 대신 뜻이 있는 이름을 돌려준다. 응답 길이에 상한 | 결과에 회사명을 같이. 긴 결과의 기본 상한과 "더 보려면" 안내. 도구 설명을 평가 점수로 다듬는다 |
| [Neo4j Text2Cypher](https://arxiv.org/abs/2412.10064) | 2024 | 질문-Cypher 44,387건 | 그래프 DB로 옮기지 않으면 쓸 곳이 없다 |
| [GQLBench](https://aclanthology.org/2026.acl-long.1476/) | ACL 2026 | 그래프 질의어 생성의 평균 실행 정확도 35.40% | 경로 질문은 함수로 고정한다는 설계의 근거 |

### 이 갈래의 시사점

1. **판단: 지금은 조회 함수만. 더한다면 규칙이 적용된 뷰 위에서만 SQL. 원본 표 SQL은 하지 않는다.**
   순서는 조회 함수 ≥ 뷰 위 SQL > 원본 표 SQL. 조사한 자료 중 원본 표 SQL을 지지하는 것은 없었다
2. **정확도보다 "틀리는 방식"이 중요하다.** 함수는 못 하면 실패하고, SQL은 그럴듯한 틀린 숫자를 낸다.
   분석가는 SQL을 검토하지 않으므로 조용한 오답이 그대로 쓰인다
3. **엇갈리는 근거: 모델이 좋아지면서 SQL 쪽 격차가 빠르게 줄고 있다.** SQL을 쓰는 능력은 거의 해결됐고,
   숨은 업무 규칙을 아는 능력은 해결되지 않았다. 우리 문제는 뒤쪽이다
4. **SQL 도구를 더한다면** 조회 계층의 규칙을 MySQL 뷰로 굳히고, 읽기 전용 계정으로 그 뷰만 보게 한다.
   함수로 안 되는 집계형 질문에만 쓰고, 경로 탐색은 넘기지 않는다
5. **같은 문항을 세 설정(함수만 / 뷰 위 SQL / 원본 표 SQL)으로 돌리면 이 판단을 우리 데이터로 증명할 수 있다**

---

## 4. Agent 설계와 평가 실무 (25건)

### 설계 지침

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | 2024, Anthropic | 코드가 순서를 정하는 "워크플로"와 LLM이 다음 도구를 고르는 "에이전트"를 구분. 단계를 미리 정할 수 없는 문제에만 에이전트 | 순서가 거의 고정인 질문은 고정 경로로, 경로 탐색 같은 열린 질문만 에이전트로. 기업 인자는 이름이 아니라 기업 찾기가 준 코드만 받는다 |
| [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | 2025, Anthropic | 소수의 도구, 뜻이 있는 식별자, 응답 길이 상한. 도구 설명을 평가로 다듬되 떼어 둔 시험 세트를 쓴다 | 기본 건수 제한과 "더 있음" 표시. 지표에 도구 호출 수와 도구 오류 수 |
| [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | 2025, Anthropic | 컨텍스트는 한정된 주의력 예산 | 공시 원문을 싣지 않고 번호와 제목만 주고, 필요할 때 상세를 가져온다 |
| [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) | 2024, Anthropic | 조각마다 문맥 설명을 붙여 색인. 검색 실패율 5.7% → 1.9% | 공시 본문 검색을 붙일 때 |
| [12-Factor Agents](https://github.com/humanlayer/12-factor-agents) | 2025 | 쓰이는 에이전트는 "대부분 그냥 소프트웨어" | 거절과 근거 번호 유무는 코드로 사후 검사한다 |

### 평가 방법

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | 2026, Anthropic | 실패에서 뽑은 과제 20~50개가 좋은 출발. "능력 평가"(어려운 문항)와 "회귀 평가"(거의 100%를 유지해야 하는 문항)를 나눈다. 시행당 75%면 3번 모두 성공은 약 42% | 기준선이 맞힌 문항은 회귀 세트로. 문항마다 2~3회 반복. 거절해야 할 질문과 비슷하지만 답해야 할 질문을 짝으로 |
| [Adding Error Bars to Evals](https://www.anthropic.com/research/statistical-approach-to-model-evals) | 2024, Anthropic | 신뢰구간을 함께 보고. 같은 문항으로 비교할 때는 문항별 차이를 본다. 연관된 문항 묶음을 무시하면 오차가 3배 넘게 과소평가 | 모델 비교는 문항별 승패 표로. 같은 기업에서 나온 문항은 묶음으로 센다 |
| [문항이 수백 개 미만이면 정규 근사를 쓰지 말라](https://arxiv.org/abs/2503.01747) | ICML 2025 | 작은 표본에서는 흔한 오차 막대가 너무 좁다. Wilson 구간이나 베이즈 방식을 쓴다 | 우리 규모에 맞는 방법 |
| [ALCE](https://arxiv.org/abs/2305.14627) | EMNLP 2023 | 인용 재현율(문장이 인용한 출처로 뒷받침되는가)과 인용 정밀도(쓸모없는 인용이 없는가) | "근거" 점수를 이 둘로 나눈다. 접수번호가 있어 코드로 채점할 수 있다 |
| [RAGAS](https://arxiv.org/abs/2309.15217), [ARES](https://arxiv.org/abs/2311.09476) | 2023~2024 | 정답 없이 채점하는 틀. 사람 채점 표본으로 자동 채점기를 보정 | 우리는 정답을 만들 수 있어 필요가 작다 |
| [Who Validates the Validators?](https://arxiv.org/abs/2404.12272) | 2024 | 채점 기준은 실제 답을 봐야 정해진다("기준 표류") | 기준은 개발용 문항에서 확정해 고정한 뒤 시험 세트에 수정 없이 적용 |
| [Hamel Husain·Shreya Shankar의 평가 FAQ](https://hamel.dev/blog/posts/evals-faq/) | 2024~2026 | 범용 지표보다 기록을 직접 읽는 오류 분석이 먼저. 합격·불합격 이분법. 질문은 차원 표를 먼저 정하고 칸별로 만든다 | **문항을 차원 표(관계 종류 × 질문 형태 × 기업 규모)에서 뽑는다** |
| [Eugene Yan, LLM 채점기의 효과](https://eugeneyan.com/writing/llm-evaluators/) | 2024 | LLM 채점기는 긴 답과 자기 출력을 선호한다 | 정답과 빠짐없음은 코드로 채점. 채점할 때 어느 시스템의 답인지 가린다 |

### 벤치마크와 그 한계

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [AI Agents That Matter](https://arxiv.org/abs/2407.01502) | 2024, Princeton | 정확도만 보면 쓸데없이 비싼 시스템이 나온다. 단순한 기준선이 93.2% / $2.45, 복잡한 방법이 88.0% / $134.50. 조사한 17개 벤치마크 중 7개는 떼어 둔 세트가 없다 | 결과를 "정답률 대 문항당 비용" 한 그림으로. 기준선이 강했던 것도 같은 교훈 |
| [τ-bench](https://arxiv.org/abs/2406.12045) | 2024 | 같은 과제를 k번 모두 성공하는 비율. 최신 에이전트도 8번 모두 성공은 25% 미만 | 한 번 맞힌 것과 매번 맞히는 것을 구분해 보고 |
| [엄밀한 에이전트 벤치마크 점검표](https://arxiv.org/abs/2507.02825) | 2025 | 유명 벤치마크의 채점 결함. 빈 응답을 성공으로 센 경우도 있었다 | **채점기에 빈 답, "모르겠습니다", 전부 나열한 답을 넣어 본다** |
| [GAIA](https://arxiv.org/abs/2311.12983), [BrowseComp](https://arxiv.org/abs/2504.12516) | 2023, 2025 | 찾기는 어렵지만 답은 짧고 검증하기 쉬운 질문. 정답 비공개 | 답이 회사명·숫자·접수번호 목록으로 떨어지게. 시험 세트의 정답은 채점 스크립트만 읽는다 |
| [GSM1k](https://arxiv.org/abs/2405.00332) | NeurIPS 2024 | 난이도를 맞춘 새 문항으로 과적합을 잰다. 하락 최대 8% | 개발 세트 점수와 새 시험 세트 점수를 나란히 적는다 |
| [FinanceBench](https://arxiv.org/abs/2311.11944) | 2023 | 공시 질의응답. 검색을 붙인 GPT-4-Turbo가 81%를 틀리거나 거부. 문항마다 근거 문자열 | 문항 형식: 질문 / 정답 / 근거 공시 / 근거 문장. **정답 / 오답 / 답변 거부를 따로 센다** |

### 기업 사례와 거절

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [GitHub Copilot의 모델 평가](https://github.blog/ai-and-ml/generative-ai/how-we-evaluate-models-for-github-copilot/) | 2025 | 모델을 바꿀 때마다 같은 세트를 다시 돌린다 | 회귀 세트를 자동으로 돌리는 스크립트 |
| [Anthropic 다중 에이전트 조사 시스템](https://www.anthropic.com/engineering/multi-agent-research-system) | 2025 | 약 20개 질문으로 시작. 채점 항목: 사실 정확도, 인용 정확도, 빠짐없음, 출처 품질(1차 출처 우선), 도구 효율 | 우리 채점표와 거의 같다. 출처가 공시 원문인지 2차 자료인지 구분 |
| [Notion](https://braintrust.dev/blog/notion) | - | 회귀 평가와 새 모델 평가를 분리. 한국어·일본어 전용 세트 | 회사명 표기 변형(약칭, 옛 이름, 영문명) 문항 |
| [DoorDash 모의 사용자](https://www.infoq.com/news/2026/03/doordash-llm-chatbot-simulator/) | 2026 | 과거 기록으로 모의 사용자를 만든다 | 과거 기록이 없는 우리에게는 제한적 |
| [OR-Bench](https://proceedings.mlr.press/v267/cui25a.html) | ICML 2025 | 안전을 강화하면 무해한 요청까지 거절한다 | 거절 정확도와 과잉 거절률을 따로 보고 |

### 이 갈래의 시사점

1. **개발용이 된 30문항은 그대로 두고, 봉인된 시험 세트를 새로 만든다.** 개발 중 본 기업·공시와 겹치지 않게,
   정답은 채점 스크립트만 읽게, Agent 설계가 끝난 뒤 한 번만 돌린다
2. **질문을 고르는 권한을 작성자에게서 떼어 낸다.** 기업은 전체 상장사에서 무작위로, 질문 틀은 차원 표에서 칸별로,
   **정답은 우리 DB가 아니라 DART 원문을 사람이 보고** 적는다. 우리 DB에 없는 것이 정답인 문항을 일부러 넣는다
3. **실제 사용자가 없는 것은 지금 단계에서 결격이 아니다.** 기록을 전부 읽어 실패 유형을 분류하고 대리 지표를 쓴다.
   업무 지표는 시범 사용 뒤로 미룬다고 밝힌다
4. **30문항으로 말할 수 있는 범위** (조사자가 논문의 방법으로 계산한 값): 24문항 중 18개 정답의 95% 구간은 약 55~88%.
   두 시스템은 약 35%p 넘게 차이 나야 구분된다. "Sonnet이 Haiku보다 5%p 낫다", "정답률이 유지된다"는 말할 수 없다.
   모델 선택 기준을 "싼 모델만 틀린 문항이 몇 개이고 읽어 보니 받아들일 만한가"로 바꾼다
5. **채점 항목 넷은 업계 관행과 같다.** 더할 것: 근거를 재현율과 정밀도로 나누기, 정답 / 오답 / 답변 거부를 따로 세기,
   거절 문항을 짝으로 두기, 채점기 자체를 먼저 시험하기

---

## 5. 이름 연결, 구조화 추출과 검증, 데이터 품질 (20건)

### 이름 연결 (개체 해소)

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [Entity Matching using LLMs](https://arxiv.org/abs/2310.11244) | EDBT 2025 | 학습 데이터 없는 GPT-4가 전용 모델과 비슷하거나 낫다(F1 76~96). 못 본 대상에서는 전용 모델이 크게 무너진다 | 정확 일치에 실패한 건만 후보 몇 개와 문맥(공시 회사, 업종, 국가)을 붙여 LLM에 묻는 2단 구조 |
| [Jellyfish](https://arxiv.org/abs/2312.01678) | EMNLP 2024 | 전처리용 로컬 LLM | 지금 규모에서는 불필요 |
| [Splink](https://moj-analytical-services.github.io/splink/) | 영국 법무부 | 항목별 일치·불일치에 가중치를 주는 확률 모형 | 불일치 증거(국가, 업종, 상장 상태가 다름)에도 감점. 사이 구간은 보류 |
| [OpenSanctions](https://www.opensanctions.org/docs/identifiers/) | 운영 중 | 여러 출처의 대상을 합치고 고정 대표 ID를 준다. 합쳐진 옛 ID를 지우지 않고 남긴다. 이름이 맞아도 국가·식별자가 어긋나면 감점. 애매하면 검토 대기열 | **구조가 가장 닮은 공개 사례.** 옛 이름 → 현재 ID 전달 줄, 자동 확정 / 검토 대기 / 기각 3단, 모든 연결에 근거 |
| [Senzing](https://senzing.com/what-is-entity-resolution/) | - | 확신이 없으면 합치지 않고 "가능 후보"로 둔다. 맞아 보이지만 틀린 연결을 "보이지 않는 오탐"이라 부른다 | 연결마다 사유 코드(정확 일치, 법인등록번호, LLM 판정)를 저장 |
| [GLEIF (LEI)](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-who-owns-whom) | 운영 중 | 전 세계 법인 식별자, 무료. **한국 주소 LEI는 2,781건, 유효 1,419건, 그중 802건은 펀드**(조사자가 API로 직접 조회). "HMM"으로 검색하면 19개 법인 | 한국 기업의 주 식별자로는 못 쓴다. 해외 법인의 후보 사전으로는 쓸 만하다(국가 필터) |
| [OpenDART 기업개황·고유번호](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019002), [금융위 KRX상장종목정보](https://www.data.go.kr/data/15094775/openapi.do), [금융위 기업기본정보](https://www.data.go.kr/data/15043184/openapi.do) | 운영 중 | 한국에서는 법인등록번호가 사실상의 표준 키. 이름 변경 이력 전용 API는 찾지 못함 | 상장종목정보를 여러 기준일로 받아 "번호는 같은데 이름이 달라진 시점"을 뽑으면 이름 변경 이력이 된다(과거 범위는 시험 필요). 고유번호 파일의 최종변경일이 바뀐 회사만 다시 조회 |

### 구조화 추출과 근거 확인

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [LangExtract](https://github.com/google/langextract) | Google | 추출값마다 원문의 글자 위치를 붙인다. 긴 문서는 여러 번 훑는다 | 우리 계획과 같은 설계. 근거를 문장이 아니라 시작·끝 글자 위치로 저장 |
| [Financial Report Chunking](https://arxiv.org/abs/2402.05131) | 2024 | 문서 구조 단위로 자른다. 개선 폭은 작다(41.84% → 43.97%) | 절 제목 기준으로 자르고 표는 통째로. 조각에 "회사 > 절" 경로를 붙인다 |
| [Docling](https://arxiv.org/abs/2408.09869) | IBM, 2024 | PDF의 표를 인식 | DART 원문은 이미 태그가 있어 불필요 |
| [Anthropic Citations](https://platform.claude.com/docs/en/build-with-claude/citations), [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) | 공식 문서 | 구조화 출력은 형식만 보장한다. 값이 맞는지는 보장하지 않는다. Citations와 구조화 출력은 함께 못 쓴다 | 형식 보장과 사실 보장은 다르다. 대조 전에 공백·전각 문자를 정규화 |
| [케임브리지 공급망 LLM 추출](https://arxiv.org/html/2408.07705v1), [MIT 공급망 지도](https://ctl.mit.edu/publications/supply-chain-mapping-through-retrieval-augmented-generation-applications-electronics) | 2024, 2026 | 대상 인식 0.95, **관계 추출 0.82**. 틀리는 곳은 이름이 아니라 관계 쪽 | 관계의 방향(누가 고객인가)을 따로 검증. 같은 입력을 여러 번 돌려 결과 변동을 기록 |

### 검증, 데이터 품질, 출처

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [Self-Consistency](https://arxiv.org/abs/2203.11171) | ICLR 2023 | 여러 번 뽑아 가장 많이 나온 답 | 2~3회 추출에 모두 나온 것만 확정, 나머지는 보류. 애매한 절에만 |
| [XBRL Calculations 1.1](https://www.xbrl.org/Specification/calculation-1.1/REC-2023-02-22/calculation-1.1-REC-2023-02-22.html) | 2023 표준 | 합계 검산의 표준. 반올림된 값을 점이 아니라 구간으로 보고 겹치면 일치 | **우리 교차 확인에 대응하는 표준.** 허용 오차를 적힌 자릿수의 반올림 구간으로 정의. 버림 방식도 따로 |
| [Great Expectations](https://docs.greatexpectations.io/docs/core/introduction/gx_overview), [dbt data tests](https://docs.getdbt.com/docs/build/data-tests) | 운영 중 | 검사 = "실패하는 줄을 고르는 SELECT 문". 0줄이면 통과 | 검사를 SQL 파일로 통일하고 실패 줄을 품질 기록에 넣는다. 기록을 검사 이름 / 실행 / 대상 건수 / 실패 건수로 |
| [W3C PROV](https://www.w3.org/TR/prov-overview/) | 2013 표준 | 무엇에서 나왔나, 무엇이 만들었나 | 관계 줄마다 접수번호 + 절 + 위치, 추출기 버전 + 실행 ID |
| [지식 그래프 정확도의 효율적 추정](https://mail.vldb.org/pvldb/volumes/17/paper/Efficient%20and%20Reliable%20Estimation%20of%20Knowledge%20Graph%20Accuracy) | VLDB 2024 | 정확도가 100%에 가까우면 흔한 신뢰구간이 깨진다. Wilson 구간과 군집 표본 | 일치율을 구간과 함께 보고. 정답지가 없는 관계는 무작위 표본 100~200건을 손으로 판정 |
| [EDGAR-CORPUS](https://arxiv.org/abs/2109.14394) | 2021 | 연차보고서를 항목별로 잘라 배포 | 원문은 한 번 받고 필요한 절만 잘라 저장. 추출기를 고쳐도 다시 받지 않는다 |

(지식 그래프 품질 관리 서베이, TKDE 2022는 출처를 열지 못해 뺐다.)

### 이 갈래의 시사점

1. **이름을 키로 쓰지 않는다. 식별자가 키이고 이름은 증거다.** 불일치 증거는 감점하고 애매하면 보류한다
   - 이름 변경: 옛 이름을 지우지 않고 현재 ID로 가는 줄로 남긴다. 별칭에 유효 기간과 종류(현재·옛 이름·영문·약칭)
   - 약어 오연결: 짧은 영문 약어는 정확히 맞아도 자동 확정하지 않는다.
     **가장 싼 규칙: 상대 이름이 그 회사 자신의 계열회사 표(법인등록번호가 있다)에 있으면 그쪽을 먼저 연결한다**
   - 상장폐지 회사와 겹침: 기업에 존속 기간을 두고, 공시 기준일에 존재하지 않던 후보를 뺀다
2. **한국에서는 LEI가 아니라 법인등록번호가 키다**
3. **우리 교차 확인에는 표준 이름이 있다.** 비율 검산은 산술 일관성 검증, 양쪽 공시 대조는 중복 보고의 일관성, 회사수 대조는 통제 합계
4. **형식 보장과 사실 보장은 다르다.** 근거 문장이 원문에 있어도 관계의 방향이 틀릴 수 있다
5. **내려받기와 추출을 분리한다.** 관계 줄마다 접수번호 / 절 / 위치 / 추출기 버전을 붙인다

---

## 6. 금융 지식 그래프, 공시에서의 관계 추출, 공급망 관계 (25건)

### 금융 지식 그래프와 관계 추출

| 이름 | 연도·발표처 | 핵심 | 우리에게 |
|---|---|---|---|
| [FinDKG](https://arxiv.org/abs/2407.10909) | ICAIF 2024 | 뉴스 약 40만 건에서 (주체, 관계, 대상, 시점)을 LLM으로 뽑아 시간에 따라 변하는 그래프. 큰 모델로 정답을 만들고 작은 모델을 미세조정 | 시점이 붙은 관계 구조. 전체로 넓힐 때 추출 비용을 줄이는 방법 |
| [FinReflectKG](https://arxiv.org/html/2508.17906v2) | ICAIF 2025 | (1갈래와 같은 건) 10-K에서 스키마 고정 추출. 관계 10종: 지분, 공급, 제휴, 생산, 사업 지역 등 | 관계 종류 설계의 참고 |
| [Neo4j·DeepLearning.AI 강좌](https://learn.deeplearning.ai/courses/knowledge-graphs-rag/lesson/5/constructing-a-knowledge-graph-from-text-documents) | 2024 | SEC 서류로 그래프를 만들고 관계마다 근거 문단을 연결 | 관계 → 원문으로 바로 가는 근거 표 |
| [REFinD](https://arxiv.org/abs/2305.18322) | 2023, JPMorgan | SEC 보고서 문장 약 29,000건, 관계 22종. **금융 고유 관계(지분 등)는 F1 30% 미만**, 일반 관계는 70% 초과. 모델은 방향 혼동에 약하다 | 방향 혼동(누가 누구에게 납품하나)을 별도 오류 유형으로 잰다 |
| [FinRED](https://arxiv.org/abs/2306.03736) | 2022 | 이미 아는 관계를 정답으로 삼아 문장에 자동 라벨 | 등급 1의 지분·계열을 정답으로 본문 추출 평가셋을 싸게 만든다 |
| [DocLLM](https://arxiv.org/abs/2401.00908) | 2023, JPMorgan | 글자 위치를 함께 읽는 문서 모델 | DART는 태그가 있어 불필요 |

### 공급망 관계를 남들은 어떻게 채우나

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [Cohen & Frazzini, Economic Links and Predictable Returns](https://ideas.repec.org/a/bla/jfinan/v63y2008i4p1977-2011.html) | Journal of Finance 2008 | **공급사가 공시한 주요 고객**으로 고객-공급사 쌍을 만든다. 고객 주가가 움직이면 공급사가 늦게 따라온다. 2023년 후속 연구는 발표 뒤 유의성이 사라졌다고 보고 | "한 종목이 움직이면 연결된 종목을 본다"는 쓰임의 학술적 근거. 납품 관계는 파는 쪽 공시에서 세우는 것이 정석. 관계에 매출 의존도(%)를 둔다 |
| [FactSet Revere 공급망 관계](https://www.library.hbs.edu/find/databases/factset-revere-supply-chain-relationships) | 상용 | 회사가 스스로 밝힌 관계를 모으고, **상대가 밝히지 않았어도 반대 방향으로 자동 연결**한다. 고객·공급사·파트너·경쟁사 4범주 | **"누가 밝혔나"(주체가 / 상대가 / 양쪽 다) 칸** |
| Bloomberg SPLC, [LSEG Value Chain](https://blogs.cranfield.ac.uk/library/supply-chain-bloomberg-workspace), S&P Capital IQ | 상용 | LSEG는 관계마다 0~100% 신뢰 점수를 붙인다. Bloomberg·S&P의 세부는 공식 페이지를 열지 못해 미확인 | 신뢰 등급을 화면에 보여 준다 |
| [공급망 DB의 방법론 비판](https://ideas.repec.org/a/bla/jscmgt/v59y2023i1p3-25.html) | Journal of Supply Chain Management 2023 | 상용 DB도 회사가 공개적으로 밝힌 관계만 담는다 | **"공시된 관계만 보여 줍니다. 없는 것이 관계 없음을 뜻하지 않습니다"를 화면과 답에 명시** |
| [케임브리지, LLM으로 공급망 가시성](https://arxiv.org/html/2408.07705) | 2024 | (5갈래와 같은 건) 관계 4종: 납품, 소유, **생산(회사 → 제품)**, 위치 | 회사 → 제품 관계를 넣어 "무엇을 납품하는지"로 잇는다. 사업보고서 "주요 제품" 표에서 규칙 추출 가능 |
| [MIT CTL, RAG 기반 공급망 지도](https://ctl.mit.edu/publications/supply-chain-mapping-through-retrieval-augmented-generation-applications-electronics) | 2026 | (5갈래와 같은 건) 연차보고서와 실적 발표 녹취에서 추출 | IR 자료를 등급 2 자료로 |
| [공급망 링크 예측](https://ideas.repec.org/a/taf/tprsxx/v62y2024i15p5596-5612.html) | IJPR 2024 | 그래프 신경망으로 보이지 않는 연결을 예측. 자동차 산업 데이터 | 근거가 없으므로 관계가 아니라 "후보"로만 |
| [InterCorpRel-LLM](https://arxiv.org/html/2510.09735v1) | 2025 | **근거 문서 없이 "A가 B에 납품하느냐"를 물으면 GPT-5도 F 0.2287** | Agent가 조회 없이 관계를 답하지 못하게 하는 규칙의 근거 |
| [한국기업데이터 거래 네트워크 연구](https://scholar.kyobobook.co.kr/article/detail/4010024833526) | 2016 | 신용평가사의 거래처 DB로 기업 201,586곳의 거래 관계. 자동차 업종의 거래망이 오래 지속 | 비상장 2·3차 협력사까지 가려면 신용평가사 거래처 DB가 사실상 유일한 국내 경로(구매). 자동차는 연 1회 갱신으로도 대체로 유효 |

### 금융 질의응답 벤치마크

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| [FinanceBench](https://arxiv.org/abs/2311.11944) | 2023 | (4갈래와 같은 건) | 질문 / 정답 / 근거 3종 세트. 틀린 답보다 거부가 낫다는 기준 |
| [FinDER](https://arxiv.org/abs/2504.15800) | ICLR 2025 워크숍 | 금융 전문가가 실제로 치는 짧고 약어가 많은 질문 5,703개 | 질문을 분석가의 실제 말투로("현차 1차 벤더 중…"). 약어 사전을 이름 연결에 |
| [Finance Agent Benchmark](https://arxiv.org/abs/2508.00828) | 2025 | 검색과 EDGAR 조회 도구를 준 에이전트. 최고 모델 46.8%, 질의당 평균 3.79달러 | 원문을 즉석에서 뒤지는 Agent는 절반도 못 맞힌다. 미리 구조화한 표를 조회하는 구조의 근거 |
| [TAT-QA](https://arxiv.org/abs/2105.07624) | ACL 2021 | 표와 주변 글을 함께 읽어야 풀리는 질문 | "거래처1 매출 비중" 표와 본문의 고객 이름 문장을 한 덩어리로 준다 |

### 금융사 사내 사례와 한국 자료

| 이름 | 연도 | 핵심 | 우리에게 |
|---|---|---|---|
| JPMorgan LLM Suite (2차 출처) | 2024 | 모델에 묶이지 않는 사내 플랫폼, 8개월에 사용자 20만 명 | 조회 도구와 모델을 분리 |
| BlackRock Aladdin Copilot (2차 출처) | 2025 | 도구가 40~50개를 넘으면 성능 저하. 평가를 PR마다 돌린다 | 도구를 소수로. 변경마다 문항 자동 실행 |
| [미래에셋증권 AI 리포트](https://core.asiae.co.kr/article/2024050816341247868) | 2024 | 공시에서 데이터를 얻어 초안을 만들고 애널리스트가 감수. 약 5시간 → 5~15분 | 사람 감수 흐름. 원인 후보는 Agent가 제시하고 분석가가 확정 |
| [OpenDART 주요사항보고서 API](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS005), [dart-fss](https://github.com/josw123/dart-fss), [OpenDartReader](https://github.com/FinanceData/OpenDartReader) | 운영 중 | 합병, 분할, 주식 양수도, 영업양수도, 소송이 구조화된 API로 있다. 임원 현황도 있다 | **규칙으로 뽑을 수 있는데 아직 안 쓰는 관계.** 사건형 관계, 임원 겸직 |
| [재벌 네트워크 중심성 연구](https://ideas.repec.org/a/eee/pacfin/v62y2020ics0927538x20300287.html) | Pacific-Basin Finance Journal 2020 | 그룹 내 출자 네트워크의 중심성 | 중심성을 미리 계산해 점 크기나 "핵심 계열사" 답에 |

### 이 갈래의 시사점

1. **납품 관계는 "받는 쪽"이 아니라 "파는 쪽" 공시에서 세우고 반대 방향으로 자동 연결한다.** 계획한 주요 고객 추출이 표준 경로다
2. **공시만으로 모자란 부분은 세 가지로 채우고 등급을 달리한다:** IR 자료(등급 2), 신용평가사 거래처 DB(구매), 링크 예측(후보로만)
3. **관계 종류의 표준은 고객·공급사·파트너·경쟁사에 지분·사건·인물·제품을 더한 것이다.**
   다음 확장 순서: 사건형(합병·분할·주식 양수도·소송, API가 있어 등급 1) → 회사 → 제품 → 인물-회사 → 경쟁사·제휴(LLM, 등급 2)
4. **LLM의 금융 관계 추출은 아직 약하다**(F1 30% 미만, 정확도 0.82, 규칙 준수율 64.8%). 방향 오류를 따로 잰다
5. **지금 평가는 계열 관계만 정답과 대조한다.** 공급 관계는 사람이 만든 정답 세트(예: 부품사 30곳의 실제 주요 고객)가 따로 필요하다

---

## 설계 수정 제안 (2026-10-05)

조사한 것은 여섯 갈래 130건(겹치는 것을 빼면 약 125건)이다. 아래는 조사를 마친 시점의 제안이고, 채택한 것은 DESIGN.md에 옮겼다.

### 가. 설계에서 빠져 있던 것 — 고친다

| # | 고칠 것 | 지금 | 바꾼 뒤 | 근거 |
|---|---|---|---|---|
| 1 | **우리가 언제, 어떤 추출기로 넣었나** | 기준일·공개일·무효일만 있다. 추출 규칙을 고쳐 다시 넣으면 전에 보여 준 답을 되살릴 수 없다 | 관계 줄에 적재 시각과 추출기 버전. 줄은 추가만 하고 고치지 않는다 | XTDB, Feast, SQL:2011, W3C PROV |
| 2 | **"없음"과 "모름"의 구분** | 보고서를 더 내지 않는 회사의 마지막 표가 영원히 현재로 보인다. 수집 범위 밖도 빈 결과로 나온다 | 최신 보고서가 오래되면 "오래됨" 표시. 조회 결과에 수집 범위(기간, 보고서 종류)를 함께 돌려준다. 화면과 답에 "공시된 관계만 보여 준다"를 적는다 | Feast의 유효 기한, LongMemEval의 기권, 공급망 DB 비판 |
| 3 | **이름 연결** | 정규화한 이름이 별칭과 정확히 맞으면 붙인다 | 식별자를 먼저 쓴다: 상대 이름이 그 회사 자신의 계열회사 표(법인등록번호가 있다)에 있으면 그쪽을 먼저 붙인다. 별칭에 종류와 유효 기간. 연결마다 사유. 자동 확정 / 후보 / 기각 3단 | OpenSanctions, Senzing, GLEIF |
| 4 | **누가 밝혔나** | 지분만 출처 표(출자현황 / 최대주주 현황)를 적는다 | 모든 관계에 "주체가 밝힘 / 상대가 밝힘 / 양쪽 다". 부품사만 밝힌 납품 관계를 완성차 쪽에서도 보이게 하되 그 사실을 표시 | FactSet의 reverse-link |
| 5 | **교차 확인의 허용 오차와 보고** | 자릿수에 맞춘 임의 폭. 95.7% 같은 점 하나 | 적힌 자릿수의 반올림 구간이 겹치면 일치. 일치율은 Wilson 구간과 함께 | XBRL Calculations 1.1, VLDB 2024 |
| 6 | **시점 누수 회귀 테스트** | 조회 규칙 테스트만 있다 | 임의 시점으로 조회한 결과에 그 뒤 공개된 줄이 한 건이라도 있으면 실패 | ChronoBERT 계열의 검증 방식 |

### 나. Agent — 제안한 방향을 확정하고 다듬는다

| # | 내용 | 근거 |
|---|---|---|
| 7 | **조회 함수 방식으로 간다.** 원본 표에 SQL을 쓰게 하지 않는다 | dbt 2026(원본 SQL 64.5%, 의미 계층 98~100%), data.world, Snowflake. 원본 표 SQL을 지지하는 자료는 없었다 |
| 8 | 도구 설계: 시점은 필수 인자, 기업은 이름이 아니라 기업 찾기가 준 코드로, 결과는 대상·날짜 순으로 정렬, 건수 상한과 "더 있음" 표시, 경로는 한 줄 문장으로, 기간 계산과 순서는 코드가 | Test of Time(순서만 바꿔도 73.57% → 45.71%), Anthropic 도구 글, GNN-RAG |
| 9 | 답에 인용한 접수번호가 실제 도구 결과에 있었는지 코드로 사후 검사. 조회 없이 관계를 말하지 못하게 한다 | 12-Factor Agents, InterCorpRel-LLM(근거 없이 물으면 F 0.23) |
| 10 | 순서가 고정인 질문은 고정 경로로 처리하는 구성을 비교 대상에 넣는다 | Building effective agents |
| 11 | SQL 도구는 나중에, 조회 규칙을 MySQL 뷰로 굳힌 위에서만. 같은 문항을 세 설정(함수 / 뷰 위 SQL / 원본 표 SQL)으로 돌려 우리 데이터로 보인다 | dbt 2026, Databricks Genie, GQLBench(경로는 SQL로 넘기지 않는다) |

### 다. 평가 — 가장 많이 바뀐다

| # | 내용 | 근거 |
|---|---|---|
| 12 | **봉인된 시험 세트를 새로 만든다.** 지금 30문항은 개발·회귀용 | Anthropic 도구 글, AI Agents That Matter, GSM1k |
| 13 | 시험 문항을 만드는 법: 기업은 전체 상장사에서 무작위로, 질문 틀은 차원 표(관계 종류 × 질문 형태 × 기업 규모)에서 칸별로, **정답은 우리 DB가 아니라 DART 원문을 사람이 보고**. 우리 DB에 없는 것이 정답인 문항을 넣는다 | Hamel·Shankar, FinanceBench, Demystifying evals |
| 14 | 꼭 넣을 문항 종류: 같은 질문에 시점만 다른 쌍, 정정 전후로 답이 다른 문항, "그 시점에는 알 수 없었다"가 정답인 문항, 거절해야 할 질문과 비슷하지만 답해야 할 질문의 짝, 회사명 표기 변형, 분석가 말투 | MRAG, LongMemEval, LoCoMo, OR-Bench, FinDER |
| 15 | 채점: 정답 / 오답 / 답변 거부를 따로 센다. 빠짐없음. 근거는 재현율과 정밀도로 나누고 1차 출처인지 구분. 시간·토큰·도구 호출 수. 정답과 빠짐없음은 코드로 채점 | FinanceBench, ALCE, Anthropic 조사 시스템, Eugene Yan |
| 16 | 문항마다 3회 반복해 "매번 맞히는가"를 보고. 채점기에 빈 답과 전부 나열한 답을 먼저 넣어 본다 | τ-bench, 벤치마크 점검표 |
| 17 | **주장의 수위를 낮춘다.** 30문항으로는 "Sonnet이 Haiku보다 5%p 낫다", "정답률이 유지된다"고 말할 수 없다. 모델은 "싼 모델만 틀린 문항이 몇 개이고 읽어 보니 받아들일 만한가"로 고른다. 유형별로 나눠 보고한다 | Wilson 구간(24문항 중 18개 → 약 55~88%), GraphRAG-Bench |
| 18 | 기준선의 누수를 적는다: 모델이 학습 때 외운 사실, 과거 시점 질문에 최신 정보를 주는 웹 검색. 문항마다 정답이 "그 시점의 정답"인지 밝힌다 | The Memorization Problem, 금융 LLM 평가의 다섯 편향 |

### 라. 범위 — 넓히는 순서를 바꾼다

| 순서 | 내용 | 근거 |
|---|---|---|
| 1 | **사건형 관계**(타법인 주식 양수도, 합병, 분할, 영업양수도): OpenDART에 API가 있어 규칙으로 뽑는다. "왜 움직였나"에 가장 직접적이고, 기준선이 우리보다 나았던 Q23·Q27이 이 공시였다 | OpenDART 주요사항보고서 API |
| 2 | **주요 고객 추출(LLM)**: 스키마 고정, 근거를 글자 위치로, 방향을 따로 검증, 추출 → 비평 → 수정, 애매한 절만 2회 추출 합의. 사람이 만든 정답으로 재현율을 잰다. 회사 이름을 가명으로 바꾼 판과 비교하는 표본 검사 | Cohen & Frazzini, FinReflectKG, KGGen, LangExtract, Glasserman & Lin |
| 3 | 회사 → 제품 관계(사업보고서 "주요 제품" 표, 규칙) | 케임브리지 공급망 연구 |
| 4 | 반기·분기보고서, 개인 주주와 임원 | REFinD, OpenDART 임원 현황 |
| 5 | IR 자료, 신용평가사 거래처 DB 구매 검토 | FactSet, MIT CTL, KED 연구 |

### 마. 하지 않는다

- 그룹·산업 요약을 LLM으로 미리 만들기 (LazyGraphRAG, GraphRAG-Bench: 비용 대비 이득이 없다)
- LightRAG 같은 프레임워크 도입 (독립 평가에서 성능이 불안정하고 시간 모델을 얹기 어렵다)
- 그래프 신경망 링크 예측을 관계로 쓰기 (근거가 없다. 후보로만)
- LLM에게 두 답 중 나은 것을 고르게 하는 평가를 대표 지표로 쓰기 (순서만 바꿔도 30% 넘게 달라진다)
- LLM이 사실 저장소를 고치는 기억 구조 (과거를 재현할 수 없다)
- PDF 표 인식 도구 (DART 원문은 태그가 있다)

### 바. 그대로 둔다 — 조사로 근거를 얻은 것

- 규칙 우선 추출, LLM은 글로만 적힌 곳에 (LLM이 만든 그래프는 정답의 약 65%만 담고, 공시에서의 정밀도는 39%)
- 기준일·공개일·무효일, 지우지 않고 무효 표시 (조사한 Graph RAG 17건 중 이 구조를 다룬 것은 없었다)
- 정답과 대조하는 평가, 기준선을 먼저 재는 것
- MySQL에서 시간 칸을 직접 관리하는 것 (MySQL에는 내장 기능이 없다)
- 조회 계층에 규칙을 모으는 것 (TempReason: 문서를 주면 8.5, 정리된 사실을 주면 47.5)
- 법인등록번호를 키로 쓰는 것 (한국에서는 LEI가 얇다)
