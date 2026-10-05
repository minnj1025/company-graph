"""공정위 기업집단포털(공공데이터포털). 계열 관계의 정답 대조용."""
import xml.etree.ElementTree as ET

import requests

from .config import secret

URL = "https://apis.data.go.kr/1130000/affiliationCompSttusList/affiliationCompSttusListApi"


def affiliates(designation_ym: str) -> list[dict]:
    """지정년월 기준 대규모기업집단 소속회사 전체."""
    rows, page = [], 1
    while True:
        try:
            r = requests.get(URL, params={"serviceKey": secret("DATA_GO_KR_API_KEY"), "pageNo": page,
                                          "numOfRows": 1000, "presentnYear": designation_ym}, timeout=60)
        except requests.RequestException:
            raise RuntimeError("공정위 소속회사 조회 실패") from None
        r.encoding = "utf-8"
        root = ET.fromstring(r.text)
        if root.findtext("resultCode") != "00":
            raise RuntimeError(f"공정위 소속회사 조회: {root.findtext('resultMsg')}")
        items = root.findall("affiliationCompSttus")
        rows += [{c.tag: (c.text or "").strip() for c in item} for item in items]
        if not items or len(rows) >= int(root.findtext("totalCount") or 0):
            return rows
        page += 1
