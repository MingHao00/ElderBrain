"""联网搜索和抓页。客户端由调用方注入，本模块不读取密钥，也不建立网络连接。"""

import re
from collections.abc import Mapping, Sequence
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Protocol
from urllib.parse import unquote, urlsplit

SEARCH_LIMIT = 5
PAGE_CHAR_LIMIT = 8000
FETCH_DENIED = "这个地址不能抓取。"
PAGE_TRUNCATED_NOTE = "正文已截断。"
URL_FIELD = "url"
TITLE_FIELD = "title"
CONTENT_FIELD = "content"
RESULTS_FIELD = "results"
HTTPS_SCHEME = "https"
PDF_SUFFIX = ".pdf"
LOCALHOST_NAME = "localhost"
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost")
IPV4_MAX_LABELS = 4
IPV4_BIT_WIDTH = 32
IPV4_SEGMENT_BITS = 8
IPV4_SEGMENT_MAX = 256
HOST_DECODE_ROUNDS = 2
AMBIGUOUS_HOST_MARKS = ("/", "\\", "@", " ", "\t", "\r", "\n", "\x00")
MIN_PADDED_LABEL_LENGTH = 2
PADDED_PREFIX = "0"
EMPTY_TEXT = ""
HOST_DOT = "."

_HEX_TOKEN = re.compile(r"^0x[0-9a-f]+$", re.IGNORECASE)
_DECIMAL_LABEL = re.compile(r"^[0-9]+$")
_HTTPS_TOKEN = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_PUNCTUATION = "。，、；：！？,.;:!?)]}>\"'"

IpAddress = IPv4Address | IPv6Address
UrlKey = tuple[str, str, int | None, str, str]


class SearchClient(Protocol):
    """搜索客户端。返回值是带 results 列表的映射。"""

    def search(
        self,
        query: str,
        max_results: int,
    ) -> Mapping[str, Sequence[Mapping[str, object]]]:
        """按查询词搜索，条数不超过 max_results。"""


class PageClient(Protocol):
    """抓页客户端。一次只接收一条 URL，返回正文。"""

    def fetch(self, url: str) -> str:
        """抓取这一条 URL 的正文。"""


def web_search(query: str, client: SearchClient) -> list[dict[str, str]]:
    """搜索网页，最多保留 SEARCH_LIMIT 条。

    每条只含 url、title、content。查询词去掉首尾空白后为空时不调用客户端。

    Args:
        query: 查询词。
        client: 注入的搜索客户端。

    Returns:
        最多 5 条结果。空白查询返回空列表。
    """
    term = query.strip()
    if not term:
        return []
    return _limited_hits(client.search(term, SEARCH_LIMIT))


def fetch_page(
    url: str,
    client: PageClient,
    user_text: str,
    search_results: Sequence[Mapping[str, str]],
) -> str:
    """抓取一条已经出现过的 https。不安全或没出现过时不调用客户端。

    比较时去掉片段，方案和主机小写，路径和查询串保持原样。
    不用子串包含，避免较短主机被较长主机误放行。

    Args:
        url: 要抓取的一条地址。
        client: 注入的抓页客户端。
        user_text: 本轮用户原文。
        search_results: 本轮搜索结果，每条至少含 url。

    Returns:
        正文。超过 8000 字时截断，并在后面附上固定说明。
        拒绝时返回固定短句，不包含主机或原因。
    """
    if not _is_fetchable(url):
        return FETCH_DENIED
    if not _was_seen(url, user_text, search_results):
        return FETCH_DENIED
    return _clip_page(client.fetch(url))


def _limited_hits(
    payload: Mapping[str, Sequence[Mapping[str, object]]],
) -> list[dict[str, str]]:
    rows = payload.get(RESULTS_FIELD, ())
    hits: list[dict[str, str]] = []
    for row in rows:
        if len(hits) >= SEARCH_LIMIT:
            break
        hits.append(_public_hit(row))
    return hits


def _public_hit(row: Mapping[str, object]) -> dict[str, str]:
    return {
        URL_FIELD: _as_text(row.get(URL_FIELD, EMPTY_TEXT)),
        TITLE_FIELD: _as_text(row.get(TITLE_FIELD, EMPTY_TEXT)),
        CONTENT_FIELD: _as_text(row.get(CONTENT_FIELD, EMPTY_TEXT)),
    }


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return EMPTY_TEXT


def _is_fetchable(url: str) -> bool:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != HTTPS_SCHEME:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    host = parts.hostname
    if host is None or _host_is_blocked(host):
        return False
    return not _is_pdf_path(parts.path)


def _host_is_blocked(host: str) -> bool:
    # urlsplit 不解开 %2E。先解码再判断，不做域名解析。
    name = _decoded_host(host)
    if not name or _host_is_ambiguous(name):
        return True
    if _is_blocked_hostname(name):
        return True
    address = _parsed_ip(name)
    if address is not None:
        return _is_blocked_address(address)
    if _is_non_decimal_ip(name):
        return True
    # 十进制整数和不足四段的点分地址，标准库不当成 IP。
    return _decimal_host_is_blocked(name)


def _decoded_host(host: str) -> str:
    """解开主机编码并去掉根域尾点。大小写不参与内网判断。"""
    return _percent_decoded(host).lower().rstrip(HOST_DOT)


def _percent_decoded(value: str) -> str:
    """最多解两轮。第二轮把 %252E 收成点，挡住再编码一次的写法。"""
    decoded = value
    for _round_index in range(HOST_DECODE_ROUNDS):
        opened = unquote(decoded)
        if opened == decoded:
            return decoded
        decoded = opened
    return decoded


def _host_is_ambiguous(name: str) -> bool:
    """解码后出现斜线或用户信息，就不再当成单一主机。"""
    return any(mark in name for mark in AMBIGUOUS_HOST_MARKS)


def _is_pdf_path(path: str) -> bool:
    return _percent_decoded(path).lower().endswith(PDF_SUFFIX)


def _is_blocked_hostname(name: str) -> bool:
    if name == LOCALHOST_NAME:
        return True
    return any(name.endswith(suffix) for suffix in BLOCKED_HOST_SUFFIXES)


def _parsed_ip(name: str) -> IpAddress | None:
    try:
        return ip_address(name)
    except ValueError:
        return None


def _is_blocked_address(address: IpAddress) -> bool:
    if address.is_loopback or address.is_private or address.is_link_local:
        return True
    return bool(address.is_reserved or address.is_unspecified)


def _is_non_decimal_ip(name: str) -> bool:
    # 标准库不接受八进制和十六进制。解析失败后这些写法仍要拒绝。
    if _HEX_TOKEN.match(name):
        return True
    labels = name.split(HOST_DOT)
    if len(labels) > IPV4_MAX_LABELS:
        return False
    if not all(_is_ip_label(label) for label in labels):
        return False
    return any(_is_non_decimal_label(label) for label in labels)


def _is_ip_label(label: str) -> bool:
    if _HEX_TOKEN.match(label):
        return True
    return _DECIMAL_LABEL.match(label) is not None


def _decimal_host_is_blocked(name: str) -> bool:
    """把 1 到 4 段纯十进制主机折成 IPv4。折不成时拒绝，不连出去。"""
    parts = _decimal_parts(name)
    if parts is None:
        return False
    packed = _pack_ipv4(parts)
    if packed is None:
        return True
    return _is_blocked_address(packed)


def _decimal_parts(name: str) -> tuple[int, ...] | None:
    labels = name.split(HOST_DOT)
    if not labels or len(labels) > IPV4_MAX_LABELS:
        return None
    if not all(_is_plain_decimal(label) for label in labels):
        return None
    return tuple(int(label) for label in labels)


def _is_plain_decimal(label: str) -> bool:
    if _DECIMAL_LABEL.match(label) is None:
        return False
    return not _is_non_decimal_label(label)


def _pack_ipv4(parts: tuple[int, ...]) -> IPv4Address | None:
    """非末段必须小于 256，末段占用剩下的比特。放不下就不是合法 IPv4。"""
    if any(part >= IPV4_SEGMENT_MAX for part in parts[:-1]):
        return None
    width = IPV4_BIT_WIDTH - IPV4_SEGMENT_BITS * (len(parts) - 1)
    if parts[-1] >= (1 << width):
        return None
    packed = parts[-1]
    for index, part in enumerate(parts[:-1]):
        shift = IPV4_BIT_WIDTH - IPV4_SEGMENT_BITS * (index + 1)
        packed |= part << shift
    return IPv4Address(packed)


def _is_non_decimal_label(label: str) -> bool:
    if _HEX_TOKEN.match(label):
        return True
    if len(label) < MIN_PADDED_LABEL_LENGTH:
        return False
    return label.startswith(PADDED_PREFIX)


def _was_seen(
    url: str,
    user_text: str,
    search_results: Sequence[Mapping[str, str]],
) -> bool:
    key = _url_key(url)
    if key is None:
        return False
    if key in _keys_in_text(user_text):
        return True
    return key in _keys_in_results(search_results)


def _url_key(raw: str) -> UrlKey | None:
    parts = urlsplit(raw.strip())
    host = parts.hostname
    if host is None or parts.scheme.lower() != HTTPS_SCHEME:
        return None
    return (HTTPS_SCHEME, host.lower(), parts.port, parts.path, parts.query)


def _keys_in_text(user_text: str) -> set[UrlKey]:
    keys: set[UrlKey] = set()
    for match in _HTTPS_TOKEN.finditer(user_text):
        token = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        key = _url_key(token)
        if key is not None:
            keys.add(key)
    return keys


def _keys_in_results(
    search_results: Sequence[Mapping[str, str]],
) -> set[UrlKey]:
    keys: set[UrlKey] = set()
    for item in search_results:
        key = _url_key(_as_text(item.get(URL_FIELD, EMPTY_TEXT)))
        if key is not None:
            keys.add(key)
    return keys


def _clip_page(body: str) -> str:
    # 说明接在截断正文后面，不占用 PAGE_CHAR_LIMIT。
    if len(body) <= PAGE_CHAR_LIMIT:
        return body
    return body[:PAGE_CHAR_LIMIT] + PAGE_TRUNCATED_NOTE
