"""뉴스 분석 탭의 링크 넣기: 사용자가 붙인 기사 주소에서 제목, 언론사, 기사 날짜만 읽는다.

Claude를 부르지 않아 무료다. 페이지는 읽는 동안만 메모리에 두고 제목·언론사·날짜·주소 말고는 아무것도 남기지 않는다(본문은 저장하지 않는다).

서버가 남의 주소를 대신 여는 일이라, 그 주소로 서버 안쪽(사설망, 클라우드 메타데이터 주소 등)을 열게 하는 공격을 막는다.
- http·https 와 기본 포트(80·443)만, 주소 안의 사용자 이름·비밀번호는 받지 않는다
- 이름을 IP로 바꾼 뒤 그 IP가 모두 공용 인터넷 주소일 때만, 그리고 검사한 바로 그 IP로 접속한다(검사와 접속 사이에 이름이 바뀌어도 안쪽으로 가지 않는다)
- 넘겨주기(redirect)는 4번까지, 매번 같은 검사를 다시 한다
- HTML만, 앞 600KB까지만, 8초 안에
"""

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.parse
from datetime import date, datetime
from html.parser import HTMLParser

from .news_search import KST, clean_title, kst_today

MAX_BYTES, TIMEOUT, MAX_HOPS = 600_000, 8, 4
KEEP_SECONDS = 3600
UA = "Mozilla/5.0 (research; company-graph)"
DATE_KEYS = ("article:published_time", "og:article:published_time", "datepublished", "pubdate", "publishdate", "publish-date",
             "article.published", "dc.date.issued", "date")
_kept: dict[str, tuple[float, dict]] = {}


class LinkError(Exception):
    """사용자에게 그대로 보여 줄 까닭."""


def _public_ip(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise LinkError("그 주소의 사이트를 찾지 못했습니다. 주소를 확인해 주세요")
    addrs = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
    # ::ffff:127.0.0.1 처럼 IPv6 안에 넣은 IPv4 는 IPv4 로 보고 검사한다
    plain = [a.ipv4_mapped if a.version == 6 and a.ipv4_mapped else a for a in addrs]
    if not plain or not all(a.is_global for a in plain):
        raise LinkError("열 수 없는 주소입니다")
    return str(sorted(plain, key=lambda a: (a.version, str(a)))[0])   # IPv4 를 먼저


def _check(url: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise LinkError("http:// 나 https:// 로 시작하는 기사 주소를 넣어 주세요")
    if parts.username or parts.password:
        raise LinkError("열 수 없는 주소입니다")
    try:
        port = parts.port
    except ValueError:
        raise LinkError("열 수 없는 주소입니다")
    if port not in (None, 80, 443):
        raise LinkError("열 수 없는 주소입니다")
    if parts.hostname.endswith("news.google.com"):
        raise LinkError("Google 뉴스 링크는 원문을 연 뒤 언론사 주소를 넣어 주세요. 위의 뉴스 찾기로 찾은 기사는 바로 분석할 수 있습니다")
    return parts


class _Pinned(http.client.HTTPSConnection):
    """검사를 마친 IP로 접속하고, 인증서와 SNI 는 원래 이름으로 확인한다."""

    def __init__(self, host: str, ip: str, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedPlain(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def _get(url: str) -> tuple[str, bytes, str | None]:
    """넘겨주기를 따라가며 HTML 앞부분을 받는다. (마지막 주소, 바이트, 헤더의 charset)"""
    for _ in range(MAX_HOPS + 1):
        parts = _check(url)
        https = parts.scheme == "https"
        port = parts.port or (443 if https else 80)
        ip = _public_ip(parts.hostname, port)
        conn = (_Pinned(parts.hostname, ip, port=port, timeout=TIMEOUT, context=ssl.create_default_context()) if https
                else _PinnedPlain(parts.hostname, ip, port=port, timeout=TIMEOUT))
        path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            conn.request("GET", path, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "ko,en;q=0.5"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
                url = urllib.parse.urljoin(url, response.getheader("Location"))
                continue
            if response.status != 200:
                raise LinkError(f"그 사이트가 기사를 주지 않았습니다 (응답 {response.status}). 제목을 직접 넣어 주세요")
            kind = response.getheader("Content-Type") or ""
            if "html" not in kind.lower():
                raise LinkError("기사 페이지(HTML)가 아닙니다")
            charset = re.search(r"charset=([\w-]+)", kind, re.I)
            return url, response.read(MAX_BYTES), charset.group(1) if charset else None
        except (OSError, http.client.HTTPException):
            raise LinkError("그 사이트에 닿지 못했습니다. 제목을 직접 넣어 주세요")
        finally:
            conn.close()
    raise LinkError("넘겨주기가 너무 많은 주소입니다")


class _Head(HTMLParser):
    """meta 태그, <title>, JSON-LD 의 datePublished, 네이버 뉴스의 data-date-time 만 모은다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title, self.in_title, self.in_ld, self.ld, self.stamps = "", False, False, [], []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "title":
            self.in_title = True
        elif tag == "script" and "ld+json" in a.get("type", ""):
            self.in_ld = True
            self.ld.append("")
        elif tag == "time" and a.get("datetime"):
            self.stamps.append(a["datetime"])
        if a.get("data-date-time"):
            self.stamps.append(a["data-date-time"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "script":
            self.in_ld = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif self.in_ld:
            self.ld[-1] += data


def _decode(raw: bytes, charset: str | None) -> str:
    found = charset or (re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4000], re.I) or [None, b""])[1].decode() or "utf-8"
    try:
        return raw.decode(found, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _to_day(value: str) -> date | None:
    value = value.strip()
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (moment.astimezone(KST) if moment.tzinfo else moment).date()
    except ValueError:
        pass
    m = re.search(r"(20\d\d)[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})", value)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _ld_dates(blocks: list[str]) -> list[str]:
    out = []
    for block in blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if isinstance(node.get("datePublished"), str):
                    out.append(node["datePublished"])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return out


def read(html: str) -> dict:
    """페이지에서 제목, 언론사, 기사 날짜. 날짜를 못 찾으면 day 는 None."""
    head = _Head()
    head.feed(html)
    meta = head.meta
    source = (meta.get("og:site_name") or meta.get("application-name") or "").strip() or None
    if source and re.search(r"네이버|다음|Daum|Naver", source):   # 포털 기사는 실제 언론사가 따로 적혀 있다 (og:article:author '한국경제 | 네이버')
        press = (meta.get("og:article:author") or "").split("|")[0].strip() or meta.get("twitter:creator", "").lstrip("@").strip()
        source = press or source
    if source and " - " in source:   # '미래를 보는 창 - 전자신문'
        source = source.rsplit(" - ", 1)[1].strip()
    title = " ".join((meta.get("og:title") or meta.get("twitter:title") or head.title).split())
    if source:   # "제목 | 언론사", "제목 - 언론사", "제목 : 언론사"
        title = re.sub(rf"\s*[-|:｜–—]\s*{re.escape(source)}\s*$", "", title).strip()
    title = clean_title(title)
    candidates = [meta[k] for k in DATE_KEYS if k in meta] + _ld_dates(head.ld) + head.stamps
    day = next((d for d in map(_to_day, candidates) if d), None)
    if day and day > kst_today():
        day = None
    return {"title": title[:200], "source": source[:60] if source else None, "day": day.isoformat() if day else None}


def look(url: str) -> dict:
    """기사 주소 → {title, source, day, link}. 안 되면 LinkError (까닭은 화면에 그대로 보인다)."""
    url = url.strip()
    kept = _kept.get(url)
    if kept and time.time() - kept[0] < KEEP_SECONDS:
        return kept[1]
    final, raw, charset = _get(url)
    found = read(_decode(raw, charset))
    if len(found["title"]) < 5:
        raise LinkError("그 페이지에서 기사 제목을 찾지 못했습니다. 제목을 직접 넣어 주세요")
    found["link"] = final
    if len(_kept) > 500:
        _kept.clear()
    _kept[url] = (time.time(), found)
    return found
