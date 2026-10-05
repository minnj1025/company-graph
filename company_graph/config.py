"""환경 설정. 비밀 값은 환경 변수에서만 읽고, 어디에도 출력하지 않는다."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"


def _load_dotenv():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key, value)


_load_dotenv()

# .env 에 COMPANY_GRAPH_DB_URL 이 없으면 설치가 필요 없는 SQLite 파일을 쓴다
DB_URL = os.environ.get("COMPANY_GRAPH_DB_URL", f"sqlite:///{(DATA_DIR / 'company_graph.db').as_posix()}")

SCOPE_GROUP = "현대자동차"   # 공정위 기업집단명
SCOPE_INDUTY_PREFIX = "30"   # 자동차 및 트레일러 제조업
FTC_DESIGNATION_YM = "202605"


def secret(name: str) -> str:
    value = os.environ.get(name)
    if not value and sys.platform == "win32":
        # 셸을 연 뒤에 등록한 사용자 환경 변수는 os.environ에 없다
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            value = None
    if not value:
        raise RuntimeError(f"환경 변수 {name} 가 없습니다")
    return value
