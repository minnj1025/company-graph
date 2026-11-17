"""답에 쓰지 않기로 한 표현을 찾는다. Claude API는 부르지 않는다.

지시문(agent.SYSTEM)은 '관련주', '수혜', '실시간', 매매 권유, 주가 전망을 쓰지 않게 하지만 가끔 새어 나온다
(평가 13판: B의 W1-04 "위성 관련주도 함께 내렸습니다", D의 M14 "변압기 관련주의 상승 원인은").
agent.answer 는 ASK_WORDING=1 이면 답을 낸 뒤 이 검사에 걸린 표현을 알려 주고 한 번만 다시 쓰게 한다.

세지 않는 것:
- 따옴표·낫표 안의 글(기사 제목이나 보고서 문장을 옮긴 것)과 마크다운 링크의 글자
- 출처를 밝혀 기사를 옮긴 문장("자동 요약에 따르면 …", "… 내렸다고 전했습니다")
- 그 표현을 하지 않는다고 밝히는 문장("매수·매도 판단은 드리지 않습니다")과, 그 자체를 따지는 말("수혜 여부는", "관련주인지는")
- 질문에 있던 낱말을 되받은 것(질문이 "위성 관련주가 왜 빠졌나요"면 '관련주'는 세지 않는다)
"""
import re

# (이름, 찾는 꼴). 이름은 다시 쓰게 할 때 그대로 알려 준다
PATTERNS = [
    ("관련주", re.compile(r"관련\s?주(?![가식주택소])|관련주가|테마주")),
    ("수혜", re.compile(r"수혜")),
    ("실시간", re.compile(r"실시간")),
    ("매매 권유", re.compile(r"(?:매수|매도|비중\s?확대|비중\s?축소|분할\s?매수)\s?(?:를|을)?\s?(?:추천|권합니다|권해 드|하세요|하시길|해 보세요|하는 것이 좋|할 만합니다|기회)"
                         r"|(?:사|팔|담)(?:는|을)\s?(?:것이|게)\s?(?:좋|낫)")),
    ("주가 전망", re.compile(r"(?:오를|내릴|상승할|하락할|반등할|회복할)\s?(?:것으로|것입니다|것이다|가능성이\s?(?:높|크))")),
]
# 그 표현을 하지 않는다고 밝히는 말
REFUSAL = re.compile(r"(?:판단|말|권|단정|추천|제시|평가|언급|예측|전망)(?:하지|드리지)\s?않|(?:판단|권유|추천|전망|예측)(?:은|는)\s?(?:하지|드리지|없)|쓰지\s?않")
QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’|'[^'\n]*'|「[^」\n]*」|『[^』\n]*』|《[^》\n]*》|\[[^\]\n]*\]\([^)\n]*\)")
CITED = re.compile(r"(?:요약|기사|보도)(?:에|에서)?\s?(?:따르면|보면)|(?:다고|라고)\s?(?:전했|합니다|했습니다|보도)|(?:다는|라는)\s?(?:기사|보도)")
ASKING = re.compile(r"\s?(?:여부|인지|일지|를 볼지|을 볼지)")
SENTENCE = re.compile(r"[^.!?。\n]+[.!?。]?")


def flagged(answer: str, question: str = "") -> list[str]:
    """걸린 표현마다 '이름: 그 구절'. 같은 구절은 한 번만."""
    out = []
    for sentence in SENTENCE.findall(QUOTED.sub(" ", answer)):
        if REFUSAL.search(sentence) or CITED.search(sentence):
            continue
        for name, pattern in PATTERNS:
            for match in pattern.finditer(sentence):
                if pattern.search(question) or ASKING.match(sentence, match.end()):
                    continue
                start = max(0, match.start() - 12)
                out.append(f"{name}: …{sentence[start:match.end() + 8].strip()}…")
    return list(dict.fromkeys(out))


def feedback(found: list[str]) -> str:
    return ("표현 검사: 방금 답에 쓰지 않기로 한 표현이 있습니다: " + "; ".join(found[:8]) + ". "
            "'관련주'·'수혜'는 '○○를 만드는 회사', '○○ 사업을 한다고 보고서에 적은 회사'처럼 사실로 바꾸고, "
            "'실시간'은 언제 기준인지로, 매매 권유와 주가 전망은 빼고 확인되는 사실만 남기세요. "
            "기사 제목을 옮길 때는 따옴표로 묶고 출처를 붙입니다. 답 전체를 처음부터 다시 쓰고, 이 검사에 대해서는 답에 언급하지 않습니다.")
