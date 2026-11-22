"""联网搜索和抓页。假客户端记下调用，不访问网络，不读取密钥。"""

import sys
from pathlib import Path

import pytest

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

import web_tools
from web_tools import (
    CONTENT_FIELD,
    FETCH_DENIED,
    PAGE_CHAR_LIMIT,
    PAGE_TRUNCATED_NOTE,
    RESULTS_FIELD,
    SEARCH_LIMIT,
    TITLE_FIELD,
    URL_FIELD,
    fetch_page,
    web_search,
)

EXPECTED_SEARCH_LIMIT = 5
EXPECTED_PAGE_CHAR_LIMIT = 8000
RETURNED_ROW_COUNT = 8
DENIED_TEXT = "这个地址不能抓取。"
TRUNCATED_TEXT = "正文已截断。"
BODY_OK = "正文"
TAIL_MARKER = "TAIL-UNIQUE-MARKER"
BLANK_QUERY = " \n\t"
SEARCH_QUERY = "  spells  "
SEARCH_QUERY_CLEAN = "spells"
USER_NOTE = "库里没有"
EXTRA_FIELD = "score"
PUBLIC_URL = "https://example.com/rules/fireball"
OTHER_URL = "https://example.org/other"
UNSEEN_URL = "https://example.com/secret"
PREFIX_TEXT = "看 https://evil.com.attacker.example/phish"
PREFIX_URL = "https://evil.com/phish"
CASED_TEXT = "来源 HTTPS://Example.COM/docs/a?q=1#section"
CASED_URL = "https://example.com/docs/a?q=1"
PATH_TEXT = "https://example.com/Docs"
PATH_URL = "https://example.com/docs"
QUERY_TEXT = "https://example.com/a?q=Keep"
QUERY_URL = "https://example.com/a?q=keep"
FRAGMENT_RESULT_URL = "https://example.com/a#part"
FRAGMENT_FETCH_URL = "https://example.com/a"
PUBLIC_IP_URL = "https://8.8.8.8/dns"
PUBLIC_PACKED_URL = "https://134744072/dns"
ENCODED_PUBLIC_URL = "https://example%2Ecom/rules"
KEY_NAME = "TAVILY_API_KEY"
FORBIDDEN_SOURCE_MARKERS = (
    KEY_NAME,
    "langchain_tavily",
    "urlopen",
    "httpx",
    "requests",
    "socket",
)
REQUIRED_BLOCKED_URLS = (
    "http://example.com/page",
    "javascript:alert(1)",
    "https://example.com/manual.pdf",
    "https://127.0.0.1/secret",
    "https://192.168.0.8/a",
    "https://10.1.2.3/a",
    "https://169.254.169.254/latest",
    "https://localhost/admin",
    "https://[::1]/secret",
)
EXTRA_BLOCKED_URLS = (
    "data:text/html,hi",
    "file:///C:/Windows/win.ini",
    "https://example.com/manual.PDF",
    "https://user:pass@example.com/a",
    "https://0177.0.0.1/",
    "https://0x7f.0.0.1/",
    "https://printer.local/a",
    "https://db.internal/a",
    "https://app.localhost/a",
    "https://0.0.0.0/",
    "https://127.0.0.1./secret",
    "https://172.16.5.1/a",
    "https://[fc00::1]/a",
    "https://[::ffff:8.8.8.8]/",
    "https://0/",
    "https://2130706433/",
    "https://127.1/",
    "https://127.0.1/",
    "https://167772161/",
    "https://10.1/",
    "https://10.0.1/",
    "https://3232235521/",
    "https://192.168.1/",
    "https://2886729729/",
    "https://172.16.1/",
    "https://172.16.1281/",
    "https://2852039166/",
    "https://169.254.43518/",
    "https://256.1/",
    "https://127%2E0%2E0%2E1/",
    "https://10%2e1/",
    "https://169%2e254%2e169%2e254/latest",
    "https://%32%31%33%30%37%30%36%34%33%33/",
    "https://%32%38%35%32%30%33%39%31%36%36/",
    "https://0177%2e0%2e0%2e1/",
    "https://0x7f%2e0%2e0%2e1/",
    "https://127.0.0.1%2e/",
    "https://127%252E0%252E0%252E1/",
    "https://example.com/manual%2epdf",
)

class _FakeSearch:
    """记下搜索调用的查询词和条数上限。"""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, int]] = []

    def search(
        self,
        query: str,
        max_results: int,
    ) -> dict[str, list[dict[str, str]]]:
        self.calls.append((query, max_results))
        return {RESULTS_FIELD: self._rows}

class _FakePage:
    """记下抓页调用的那一条 URL。"""

    def __init__(self, body: str) -> None:
        self._body = body
        self.calls: list[str] = []

    def fetch(self, url: str) -> str:
        self.calls.append(url)
        return self._body

def _fetch(
    url: str,
    user_text: str,
    search_results: list[dict[str, str]] | None = None,
    body: str = BODY_OK,
) -> tuple[str, list[str]]:
    """用假客户端抓取，返回正文和调用过的 URL。"""
    client = _FakePage(body)
    found = fetch_page(
        url,
        client,
        user_text,
        [] if search_results is None else search_results,
    )
    return found, client.calls

def _hit(url: str) -> dict[str, str]:
    return {URL_FIELD: url, TITLE_FIELD: "标题", CONTENT_FIELD: "摘要"}

def test_limits_and_messages_match_the_contract() -> None:
    """条数、截断长度和两句固定文案不能改。"""
    assert SEARCH_LIMIT == EXPECTED_SEARCH_LIMIT
    assert PAGE_CHAR_LIMIT == EXPECTED_PAGE_CHAR_LIMIT
    assert FETCH_DENIED == DENIED_TEXT
    assert PAGE_TRUNCATED_NOTE == TRUNCATED_TEXT

def test_user_text_url_is_fetched_once() -> None:
    """用户原文里出现过的 https 只抓一次。"""
    result, calls = _fetch(PUBLIC_URL, user_text=f"请看 {PUBLIC_URL} 谢谢")
    assert calls == [PUBLIC_URL]
    assert result == BODY_OK

def test_search_result_url_is_fetched_once() -> None:
    """搜索结果里有、原文里没有的 https 只抓一次。"""
    result, calls = _fetch(
        PUBLIC_URL,
        user_text=USER_NOTE,
        search_results=[_hit(PUBLIC_URL)],
    )
    assert calls == [PUBLIC_URL]
    assert result == BODY_OK

def test_url_in_text_and_results_is_fetched_once() -> None:
    """两处都出现时仍然只调用一次。"""
    result, calls = _fetch(
        PUBLIC_URL,
        user_text=PUBLIC_URL,
        search_results=[_hit(PUBLIC_URL)],
    )
    assert calls == [PUBLIC_URL]
    assert result == BODY_OK

def test_seen_public_ip_is_fetched_once() -> None:
    """原文里的公网 IP 可以抓，不能把所有 IP 都当成内网。"""
    result, calls = _fetch(PUBLIC_IP_URL, user_text=f"查 {PUBLIC_IP_URL}")
    assert calls == [PUBLIC_IP_URL]
    assert result == BODY_OK


def test_seen_packed_public_ip_is_fetched_once() -> None:
    """四段公网地址的十进制整数写法仍可抓。"""
    result, calls = _fetch(PUBLIC_PACKED_URL, user_text=f"查 {PUBLIC_PACKED_URL}")
    assert calls == [PUBLIC_PACKED_URL]
    assert result == BODY_OK


def test_encoded_public_host_is_fetched_once() -> None:
    """公网主机写成百分号编码时仍可抓。"""
    result, calls = _fetch(ENCODED_PUBLIC_URL, user_text=f"查 {ENCODED_PUBLIC_URL}")
    assert calls == [ENCODED_PUBLIC_URL]
    assert result == BODY_OK

def test_unseen_public_https_is_not_fetched() -> None:
    """没出现过的公网 https 不调用客户端。"""
    result, calls = _fetch(
        UNSEEN_URL,
        user_text="没有这段地址",
        search_results=[_hit(OTHER_URL)],
    )
    assert result == DENIED_TEXT
    assert calls == []

def test_longer_host_does_not_allow_shorter_host() -> None:
    """不能用字符串包含放行更短的主机。"""
    result, calls = _fetch(PREFIX_URL, user_text=PREFIX_TEXT)
    assert result == DENIED_TEXT
    assert calls == []

def test_match_ignores_fragment_and_host_case() -> None:
    """比较时忽略片段，方案和主机小写，路径与查询保留。"""
    result, calls = _fetch(CASED_URL, user_text=CASED_TEXT)
    assert calls == [CASED_URL]
    assert result == BODY_OK

def test_search_result_fragment_still_matches() -> None:
    """搜索结果里的片段不影响同一条地址。"""
    result, calls = _fetch(
        FRAGMENT_FETCH_URL,
        user_text="无地址",
        search_results=[_hit(FRAGMENT_RESULT_URL)],
    )
    assert calls == [FRAGMENT_FETCH_URL]
    assert result == BODY_OK

def test_path_case_does_not_match() -> None:
    """路径大小写保留，不能互相放行。"""
    result, calls = _fetch(PATH_URL, user_text=PATH_TEXT)
    assert result == DENIED_TEXT
    assert calls == []

def test_query_case_does_not_match() -> None:
    """查询串大小写保留，不能互相放行。"""
    result, calls = _fetch(QUERY_URL, user_text=QUERY_TEXT)
    assert result == DENIED_TEXT
    assert calls == []

@pytest.mark.parametrize(
    "blocked_url",
    REQUIRED_BLOCKED_URLS + EXTRA_BLOCKED_URLS,
)
def test_blocked_address_skips_the_client(blocked_url: str) -> None:
    """非 https、PDF 和内网地址即使写在原文里也不抓。"""
    result, calls = _fetch(blocked_url, user_text=f"原文里写了 {blocked_url}")
    assert calls == []
    assert result == DENIED_TEXT
    assert blocked_url not in result

def test_long_page_is_clipped_after_one_fetch() -> None:
    """超过 8000 字仍只调用一次，说明附在截断正文之后。"""
    body = ("甲" * PAGE_CHAR_LIMIT) + TAIL_MARKER
    result, calls = _fetch(PUBLIC_URL, user_text=PUBLIC_URL, body=body)
    assert calls == [PUBLIC_URL]
    assert result.endswith(TRUNCATED_TEXT)
    assert TAIL_MARKER not in result
    assert result[:PAGE_CHAR_LIMIT] == "甲" * PAGE_CHAR_LIMIT
    assert len(result) == PAGE_CHAR_LIMIT + len(TRUNCATED_TEXT)

def test_page_at_limit_is_not_clipped() -> None:
    """刚好 8000 字不附加截断说明。"""
    body = "乙" * PAGE_CHAR_LIMIT
    result, calls = _fetch(PUBLIC_URL, user_text=PUBLIC_URL, body=body)
    assert calls == [PUBLIC_URL]
    assert result == body
    assert TRUNCATED_TEXT not in result

def test_search_requests_five_and_keeps_five() -> None:
    """搜索传入上限 5，客户端多返回的条目被丢掉。"""
    rows = [
        {
            URL_FIELD: f"https://example.com/{index}",
            TITLE_FIELD: f"title-{index}",
            CONTENT_FIELD: f"content-{index}",
            EXTRA_FIELD: "1",
        }
        for index in range(RETURNED_ROW_COUNT)
    ]
    client = _FakeSearch(rows)
    found = web_search(SEARCH_QUERY, client)
    titles = [item[TITLE_FIELD] for item in found]
    assert client.calls == [(SEARCH_QUERY_CLEAN, SEARCH_LIMIT)]
    assert len(found) == EXPECTED_SEARCH_LIMIT
    assert titles == [f"title-{index}" for index in range(EXPECTED_SEARCH_LIMIT)]
    assert list(found[0]) == [URL_FIELD, TITLE_FIELD, CONTENT_FIELD]
    assert f"title-{EXPECTED_SEARCH_LIMIT}" not in titles

def test_blank_query_skips_search_client() -> None:
    """查询词去掉空白后为空时不调用客户端。"""
    client = _FakeSearch([])
    assert web_search(BLANK_QUERY, client) == []
    assert client.calls == []

def test_search_runs_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有密钥环境变量时，注入的客户端仍可完成搜索。"""
    monkeypatch.delenv(KEY_NAME, raising=False)
    client = _FakeSearch([])
    assert web_search(SEARCH_QUERY_CLEAN, client) == []
    assert client.calls == [(SEARCH_QUERY_CLEAN, SEARCH_LIMIT)]

def test_module_does_not_reference_network_or_key() -> None:
    """实现本身不出现密钥名、Tavily 类和网络库。"""
    source = Path(web_tools.__file__).read_text(encoding="utf-8")
    for marker in FORBIDDEN_SOURCE_MARKERS:
        assert marker not in source
