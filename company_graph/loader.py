"""관계 줄을 넣는 한 가지 방법. 줄은 고치거나 지우지 않는다.

추출기를 다시 돌리면 새로 뽑은 줄과 지금 믿고 있는 줄을 비교해서
- 똑같은 줄은 그대로 두고
- 사라지거나 달라진 줄은 retired_at 을 적어 내리고
- 새로 생긴 줄만 더한다.
그래야 "그날 우리가 무엇을 보여 줬나"를 나중에 되살릴 수 있고, 추출기를 고친 영향이 몇 줄인지 셀 수 있다.
"""
import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from .db import Relation


def _key(r: Relation) -> tuple:
    value = None if r.value_num is None else format(Decimal(r.value_num).normalize(), "f")
    return (r.subject_company_id, r.object_company_id, r.object_name_raw, r.rel_type, value, r.value_unit,
            r.as_of_date, r.disclosed_date, r.invalidated_date, r.rcept_no, r.extract_method, r.trust_tier,
            r.evidence_text, json.dumps(r.attrs or {}, sort_keys=True, ensure_ascii=False))


def sync(db, scope: list, new_rows: list[Relation], version: str) -> dict[str, int]:
    """scope 조건에 드는 지금의 줄을 new_rows 와 같게 만든다. 넣은 수, 내린 수, 그대로 둔 수를 돌려준다."""
    now = datetime.now()
    current: dict[tuple, list[Relation]] = {}
    for row in db.scalars(select(Relation).where(Relation.retired_at.is_(None), *scope)):
        current.setdefault(_key(row), []).append(row)
    counts = {"added": 0, "retired": 0, "kept": 0}
    for row in new_rows:
        same = current.get(_key(row))
        if same:
            same.pop()
            counts["kept"] += 1
        else:
            row.ingested_at, row.extractor_version = now, version
            db.add(row)
            counts["added"] += 1
    for leftovers in current.values():
        for row in leftovers:
            row.retired_at = now
            counts["retired"] += 1
    return counts
