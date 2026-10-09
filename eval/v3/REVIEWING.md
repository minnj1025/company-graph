# 평가 문항 3판 — 문항과 정답을 검토하는 규칙

쓴 쪽과 다른 검토자가, 같은 묶음(`eval/v3/packets/<id>.json`)으로 문항(`eval/v3/gold/<id>.json`)을 다시 확인한다.
규칙은 `AUTHORING.md` 와 같다. 묶음에 있는 것만 근거로 쓴다.

## 볼 것

1. `must_include` 의 회사와 값이 묶음에 근거가 있는가. 값이 표나 글에 적힌 그대로인가
2. `must_not` 의 회사가 정말 "만드는 회사가 아닌" 곳인가 (묶음의 글로 확인)
3. 묶음에 근거가 분명한데 `must_include` 와 `others` 어디에도 없는 회사가 있는가. 있으면 넣는다
4. 질문이 한 가지로만 읽히는가. 정답을 흘리지 않는가
5. 정답이 묶음 밖의 지식에 기대고 있지 않은가

## 출력

고칠 것이 있으면 `eval/v3/gold/<id>.json` 을 직접 고친다. 그리고 `eval/v3/review/<id>.json` 에 남긴다:

```json
{"id": "P01", "verdict": "ok | fixed | dropped", "changes": ["무엇을 왜 고쳤는지. 없으면 빈 목록"], "notes": "남는 의문"}
```

`dropped` 이면 gold 의 `skip` 에 이유를 적는다.
