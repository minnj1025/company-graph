# 평가 문항 6판 — 시각화 카드를 알맞게 고르는가

쓴 날짜: 2026-10-20

Agent는 답 옆에 `show_view` 로 카드를 띄울 수 있다. kind 는 여덟 가지다: ownership(지분 구조도), group(계열 지도), supply_flow(공급망 흐름), path(두 기업의 연결 경로), timeline(공시 타임라인), contracts(계약 기간), revenue(매출 구성), returns(수익률 비교). 이 문항들은 질문에 맞는 카드를 고르는지, 카드가 필요 없는 질문에는 띄우지 않는지를 잰다.

## 문항 수

| 종류 | 문항 | 구성 |
|---|---|---|
| 카드가 필요한 질문 | 14 (V01~V14) | ownership 2(V01, V02), group 2(V03, V04), supply_flow 2(V05, V06), path 2(V07, V08), timeline 2(V09, V10), contracts 3(V06, V11, V12), revenue 1(V13), returns 1(V14). V06 은 두 카드(supply_flow, contracts)가 자연스러운 문항 |
| 카드가 필요 없는 질문 | 6 (V15~V20) | 값 하나로 끝나는 것 3(V15 최대주주, V16 종목코드, V18 시장), 거절해야 하는 것 2(V17 매수, V19 목표가), DB 범위를 묻는 것 1(V20) |
| 합계 | 20 | |

## 만든 방법

- 조회 시점(`as_of`)은 모두 2026-10-16.
- 회사는 손으로 골랐다. 이름이 알려진 상장사 20여 곳에 대해 일곱 kind 의 카드를 로컬 DB로 직접 만들어(`agent_tools.call(db, "show_view", …)`) summary 를 보고, 그 문항의 카드가 내용 있게 나오는 회사만 골랐다. 예를 들어 삼성전자는 공급계약이 50건이지만 공시 타임라인이 0건이라 timeline 문항에 쓰지 않았고, 매출 표가 한 줄뿐인 회사(SK하이닉스, LG에너지솔루션)는 revenue 문항에 쓰지 않았다. path 는 두 회사를 넣어 경로가 1개 이상 나오는 쌍만 골랐다(V07 현대자동차 → HD현대일렉트릭, V08 삼성전자 → 삼성바이오로직스. 두 번째 회사는 `note` 에 적었다).
- 각 문항의 `note` 에 고른 이유와 그때 나온 카드의 요약(건수, 대표 값)을 적었다.
- 질문은 실제 사용자 말투로 쓰고 "카드", "시각화", "그래프" 같은 말은 넣지 않았다.
- `acceptable_kinds` 는 함께 띄워도 어색하지 않은 카드다(예: 지분 구조도 질문에 계열 지도, 공급계약 상대 질문에 계약 기간이나 타임라인). revenue(V13)와 returns(V14)는 질문이 좁아 함께 띄울 카드를 두지 않았다.
- Agent의 지시문(`company_graph/agent.py`)은 읽지 않았다. 유료 API, 웹 검색, 외부 호출은 쓰지 않았다.

## 채점 기준

- 카드가 필요한 질문(`expected_kinds` 가 비지 않은 문항): Agent가 부른 `show_view` 의 kind 가 `expected_kinds` 를 하나 이상 포함하고, 그 밖의 kind 는 모두 `acceptable_kinds` 안에 있으면 맞음. `show_view` 를 부르지 않았거나, 어느 쪽에도 없는 kind 를 띄웠으면 틀림.
- 카드가 필요 없는 질문(`expected_kinds` 가 `[]`): `show_view` 를 부르지 않았으면 맞음. 한 번이라도 불렀으면 틀림.
- 카드의 대상 회사(`company_id`, path 의 `other_company_id`)가 맞는지는 이 판에서 채점하지 않는다. 기록만 해 둔다.

## 파일

- `gold/V01.json` ~ `gold/V20.json`: 문항. 칸은 `id`, `question`, `as_of`, `expected_kinds`, `acceptable_kinds`, `company`, `note`.
