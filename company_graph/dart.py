"""OpenDART 호출. 한도: 하루 20,000건, 분당 1,000회 이상이면 제한될 수 있다(DESIGN.md 4장)."""
import io
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import date

import requests

from .cache import cached_bytes, cached_json
from .config import CACHE_DIR, secret

BASE = "https://opendart.fss.or.kr/api"
REPORT_CODES = {"annual": "11011", "half": "11012", "q1": "11013", "q3": "11014"}
_MIN_INTERVAL = 0.12  # 분당 500회 이하로 유지
DAILY_BUDGET = 18_000  # 한도 20,000건에서 여유를 둔다
_lock = threading.Lock()
_last_call = 0.0


class DartError(RuntimeError):
    pass


class DailyBudgetExceeded(DartError):
    """오늘 쓸 호출을 다 썼다. 캐시가 남아 있으므로 내일 같은 명령을 다시 돌리면 이어서 받는다."""


def calls_today() -> int:
    path = CACHE_DIR / f"dart_calls_{date.today():%Y%m%d}.txt"
    return int(path.read_text()) if path.exists() else 0


def _count_call():
    count = calls_today() + 1
    if count > DAILY_BUDGET:
        raise DailyBudgetExceeded(f"오늘 DART 호출 {DAILY_BUDGET}건을 다 썼습니다")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"dart_calls_{date.today():%Y%m%d}.txt").write_text(str(count))


def _get(path: str, **params) -> requests.Response:
    global _last_call
    with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        _count_call()
    for attempt in range(3):
        try:
            return requests.get(f"{BASE}/{path}", params={"crtfc_key": secret("DART_API_KEY"), **params}, timeout=60)
        except requests.RequestException:
            if attempt == 2:
                # 예외 메시지에 인증키가 든 주소가 섞이지 않게 새로 던진다
                raise DartError(f"{path} 호출 실패") from None
            time.sleep(2)


def _json(path: str, **params) -> dict:
    body = _get(path, **params).json()
    # 013 = 조회된 데이터 없음
    if body.get("status") not in ("000", "013"):
        raise DartError(f"{path}: {body.get('status')} {body.get('message')}")
    return body


def corp_codes() -> list[dict]:
    """DART에 등록된 모든 회사의 고유번호, 이름, 종목코드."""
    z = zipfile.ZipFile(io.BytesIO(_get("corpCode.xml").content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    return [{c.tag: (c.text or "").strip() for c in item} for item in root.findall("list")]


def company(corp_code: str) -> dict:
    return cached_json("dart_company", corp_code, lambda: _json("company.json", corp_code=corp_code))


def filings(corp_code: str | None = None, *, bgn_de: str, end_de: str, **params) -> list[dict]:
    """공시 목록. corp_code 없이 부르면 기간이 3개월로 제한된다."""
    rows, page = [], 1
    while True:
        body = _json("list.json", bgn_de=bgn_de, end_de=end_de, page_count=100, page_no=page,
                     **({"corp_code": corp_code} if corp_code else {}), **params)
        rows += body.get("list", [])
        if page >= body.get("total_page", 1):
            return rows
        page += 1


def other_corp_investments(corp_code: str, bsns_year: int, report: str = "annual") -> list[dict]:
    """타법인 출자현황."""
    key = f"{corp_code}_{bsns_year}" + ("" if report == "annual" else f"_{report}")
    return cached_json("dart_other_corp_investments", key, lambda: _json(
        "otrCprInvstmntSttus.json", corp_code=corp_code, bsns_year=str(bsns_year),
        reprt_code=REPORT_CODES[report]).get("list", []))


def largest_shareholders(corp_code: str, bsns_year: int, report: str = "annual") -> list[dict]:
    """최대주주 현황."""
    key = f"{corp_code}_{bsns_year}" + ("" if report == "annual" else f"_{report}")
    return cached_json("dart_largest_shareholders", key, lambda: _json(
        "hyslrSttus.json", corp_code=corp_code, bsns_year=str(bsns_year),
        reprt_code=REPORT_CODES[report]).get("list", []))


def document(rcept_no: str) -> bytes:
    """공시 원문(XML). 캐시에만 두고 저장소에는 넣지 않는다."""
    def fetch() -> bytes:
        content = _get("document.xml", rcept_no=rcept_no).content
        if not content.startswith(b"PK"):
            # 원문이 없으면 zip 대신 <status>014</status> 같은 오류 XML이 온다
            status = re.search(rb"<status>(\d+)</status>", content)
            message = re.search(rb"<message>(.*?)</message>", content, re.S)
            raise DartError(f"document.xml {rcept_no}: {status.group(1).decode() if status else '?'} "
                            f"{message.group(1).decode('utf-8', 'replace') if message else ''}")
        z = zipfile.ZipFile(io.BytesIO(content))
        # 압축 파일에는 본문과 첨부(감사보고서 등)가 순서 없이 들어 있다. 본문은 "<접수번호>.xml"이다
        names = z.namelist()
        main = f"{rcept_no}.xml"
        return z.read(main if main in names else max(names, key=lambda n: z.getinfo(n).file_size))
    return cached_bytes("dart_document", rcept_no, ".xml", fetch)
