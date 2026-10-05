"""받아 온 원자료를 키 하나당 파일 하나로 둔다.

전체 상장사로 넓히면 하루 호출 한도 안에서 며칠에 걸쳐 받아야 한다.
키별 파일이면 끊긴 자리에서 이어 받고, 이미 받은 것은 다시 부르지 않는다.
"""
import json
from typing import Callable

from .config import CACHE_DIR


def cached_json(namespace: str, key: str, fetch: Callable[[], object]):
    path = CACHE_DIR / namespace / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def is_cached(namespace: str, key: str) -> bool:
    return (CACHE_DIR / namespace / f"{key}.json").exists()


def cached_bytes(namespace: str, key: str, suffix: str, fetch: Callable[[], bytes]) -> bytes:
    path = CACHE_DIR / namespace / f"{key}{suffix}"
    if path.exists():
        return path.read_bytes()
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data
