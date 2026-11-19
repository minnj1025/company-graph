"""매일 DART 갱신(공급계약, 지분공시, 증자·사채·자기주식 결정, 정기보고서 재무)을 이 PC에서 돌려 서비스 DB에 넣는다.

GitHub 서버(미국)에서 부르면 DART가 한참 늦게 답하거나 접속을 받지 않는 날이 있어(2026-11: 단계마다 시간 제한에 걸려 잘림),
한국에 있는 PC의 예약 작업으로 돌린다. 하는 일은 .github/workflows/financials.yml 과 같다(그 작업은 손으로 돌릴 때만 쓴다).

실행: python -m company_graph.daily_dart
- 대상 DB는 .env 의 TARGET_DB_URL(서비스 DB)이다. 주소는 어디에도 출력하지 않는다
- 단계마다 시간 제한을 두고, 한 단계가 늦거나 실패해도 다음 단계는 돈다
- 기록: data/logs/dart_YYYYMMDD.log
"""
import os
import subprocess
import sys
import time
from datetime import datetime

from .config import DATA_DIR, ROOT

STEPS = [   # (모듈, 인자, 제한 분). 뉴스 분석이 쓰는 공급계약을 먼저, 오래 걸리는 재무를 마지막에
    ("extract_supply", ["update", "3"], 20),
    ("holdings", ["update", "3"], 10),
    ("capital", ["update", "3"], 20),
    ("financials", ["update"], 25),
]


def main() -> int:
    target = os.environ.get("TARGET_DB_URL")
    if not target:
        print("환경 변수 TARGET_DB_URL 이 없습니다(.env)")
        return 1
    env = {**os.environ, "COMPANY_GRAPH_DB_URL": target, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    logs = DATA_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    failed = 0
    with open(logs / f"dart_{datetime.now():%Y%m%d}.log", "a", encoding="utf-8") as log:
        for module, args, minutes in STEPS:
            started = time.monotonic()
            log.write(f"\n== {datetime.now():%Y-%m-%d %H:%M:%S} {module} {' '.join(args)}\n")
            log.flush()
            try:
                code = subprocess.run([sys.executable, "-m", f"company_graph.{module}", *args], cwd=ROOT, env=env,
                                      stdout=log, stderr=subprocess.STDOUT, timeout=minutes * 60).returncode
                result = "끝" if code == 0 else f"실패(코드 {code})"
            except subprocess.TimeoutExpired:
                code, result = -1, f"{minutes}분 제한에 걸려 멈춤"
            failed += code != 0
            log.write(f"-- {module}: {result}, {time.monotonic() - started:.0f}초\n")
            log.flush()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
