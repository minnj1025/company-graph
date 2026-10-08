# 기준선: 웹 검색만 쓰는 Claude

2판 문항 가운데 종류마다 앞의 두 문항(32문항)을, 이 DB 없이 웹 검색과 웹 페이지 읽기만 쓸 수 있는 Claude(Opus 5.5)에게 풀게 한다.
문항마다 새로 띄우고, 다른 문항이나 정답을 보지 못하게 한다.

## 푸는 쪽에 주는 지시

- 쓸 수 있는 도구는 WebSearch 와 WebFetch 뿐이다. 이 컴퓨터의 파일을 읽거나 명령을 실행하지 않는다 (답을 적는 파일 하나만 쓴다).
- 질문의 "조회 시점"까지 공시된 내용을 기준으로 답한다.
- 찾지 못했으면 지어내지 않고 찾지 못했다고 적는다.
- 답을 `eval/v2/baseline/<id>.json` 에 적는다:

```json
{"id": "T01-01", "answer": "한국어로 쓴 답", "confidence": "높음 | 중간 | 낮음", "sources": ["근거로 쓴 주소"], "searches": 0, "fetches": 0, "fetch_failures": 0, "opened_dart_original": false}
```
