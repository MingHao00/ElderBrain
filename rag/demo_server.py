"""本地演示页。静态文件和提问都走本机，密钥留在环境变量。"""

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from alert_mail import send_alert_mail
from audit_log import (
    UNKNOWN_USER_ID,
    citation_on,
    record_rejection,
    usages_on,
    write_quietly,
)
from auth_app import AuthApp, open_auth
from auth_constants import (
    COST_ALERT_BODY,
    COST_ALERT_SUBJECT,
    ERR_ANSWER_FAILED,
    HTTP_BAD_REQUEST,
    HTTP_UNPROCESSABLE,
    PATH_ASK,
    PATH_QUERY,
    QUESTION_TOO_LONG,
)
from admin_store import published_index_version
from auth_db import AuthDb
from auth_http import Incoming, Outgoing, json_error
from auth_routes import route_api
from config import REPO_ROOT, require_env
from demo_answer import MAX_BODY_BYTES, present_answer
from idle_summary import start_idle_scan, summarize_with_qwen
from modules.published_retrieval import PublishedHybridSearch, enabled_source_ids
from modules.vector_store import DATABASE_URL_ENV, PgChunkIndex, connect_postgres
from run_generation_experiment import _public_error
from session_card import SessionCard
from system import CitedReply, ElderBrainRag, _selected_rag

WEB_DIR = REPO_ROOT / "web"
HOST = "127.0.0.1"
PORT = 8765
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
_ASK_PATHS = frozenset({PATH_ASK, PATH_QUERY})


def web_file(root: Path, url_path: str) -> Path | None:
    """只打开页面目录里的文件。问号后面的参数不参与找文件。"""
    raw = unquote(url_path.split("?", 1)[0])
    if raw in ("", "/"):
        raw = "/index.html"
    candidate = (root / raw.lstrip("/")).resolve()
    base = root.resolve()
    inside = candidate == base or base in candidate.parents
    if not inside or not candidate.is_file():
        return None
    return candidate


class DemoHandler(BaseHTTPRequestHandler):
    """一个本机页面。提问要先登录，再交给已经装好的 RAG。"""

    rag: ElderBrainRag
    auth: AuthApp

    def do_DELETE(self) -> None:
        """删除一场对话。编号在路径里，不读正文。"""
        self._write(self._routed(self._incoming(b"")))

    def do_GET(self) -> None:
        """静态页，以及会话和 GitHub 跳转。"""
        request = self._incoming(b"")
        if request.path.startswith("/api/"):
            self._write(self._routed(request))
            return
        found = web_file(WEB_DIR, request.path)
        if found is None:
            self._send(404, b"not found", "text/plain; charset=utf-8", ())
            return
        kind = CONTENT_TYPES.get(found.suffix, "application/octet-stream")
        self._send(200, found.read_bytes(), kind, ())

    def do_POST(self) -> None:
        """验证码、登出、密钥和提问。"""
        path = urlparse(self.path).path
        try:
            body = self._body()
        except ValueError as exc:
            self._write(self._early_error(path, str(exc)))
            return
        self._write(self._routed(self._incoming(body)))

    def _early_error(self, path: str, text: str) -> Outgoing:
        """长度超限时正文还没读，审计只记请求头里的长度。"""
        outgoing = _body_error(path, text)
        if outgoing.status == HTTP_UNPROCESSABLE:
            self._note_oversize(text, outgoing.status)
        return outgoing

    def _note_oversize(self, text: str, status: int) -> None:
        raw = self.headers.get("Content-Length")
        write_quietly(lambda: _record_oversize(self.auth.db, text, status, raw))

    def log_request(self, code: object = "-", size: object = "-") -> None:
        """查询串可能带授权码，日志只留路径。"""
        del size
        self.log_message("%s %s", urlparse(self.path).path, code)

    def log_message(self, fmt: str, *args: object) -> None:
        """不写客户端地址。明文 IP 不进日志。"""
        sys.stderr.write(f"{fmt % args}\n")

    def _routed(self, request: Incoming) -> Outgoing:
        try:
            return route_api(self.auth, request, self._answer, _report_failure)
        except Exception as exc:
            _report_failure(exc)
            return json_error(500, ERR_ANSWER_FAILED)

    def _answer(self, question: str, card: SessionCard) -> str:
        raw = self.rag.ask(question, card)
        # 页面文本是新字符串。校验、用量和已经通过的句子仍属于这一次。
        shown = CitedReply(present_answer(raw), citation_on(raw), usages_on(raw))
        _carry(shown, raw)
        return shown

    def _incoming(self, body: bytes) -> Incoming:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=False)
        flat = {key: values[0] for key, values in query.items() if values}
        headers = {key.lower(): value for key, value in self.headers.items()}
        return Incoming(self.command, parsed.path, flat, headers, body, self.client_address[0])

    def _body(self) -> bytes:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise ValueError("请求不是 JSON") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError(QUESTION_TOO_LONG)
        return self.rfile.read(length)

    def _write(self, outgoing: Outgoing) -> None:
        self._send(outgoing.status, outgoing.body, outgoing.content_type, outgoing.headers)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: tuple[tuple[str, str], ...],
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def _carry(shown: CitedReply, raw: str) -> None:
    """校验后的句子跟页面文本走。失败时这些字段是空的，不能把原句补回去。"""
    shown.sentences = tuple(getattr(raw, "sentences", ()) or ())
    shown.citations = tuple(getattr(raw, "citations", ()) or ())
    shown.refusal_category = getattr(raw, "refusal_category", None)
    shown.card_update = getattr(raw, "card_update", None)


def _body_error(path: str, text: str) -> Outgoing:
    status = HTTP_BAD_REQUEST
    if path in _ASK_PATHS and text == QUESTION_TOO_LONG:
        status = HTTP_UNPROCESSABLE
    return json_error(status, text)


def _record_oversize(db: AuthDb, text: str, status: int, raw: str | None) -> None:
    """失败由 write_quietly 接住，原来的 422 仍然返回。"""
    record_rejection(db, UNKNOWN_USER_ID, status, text, _declared_length(raw))


def _declared_length(value: str | None) -> int:
    # 负数和超过上限都走这里。此时还不能读正文。
    if value is None or value.strip() == "":
        raise ValueError("缺少 Content-Length")
    return int(value)


def _report_failure(exc: Exception) -> None:
    print(_public_error(exc))


def open_published_demo() -> tuple[ElderBrainRag, AuthApp]:
    """接上已发布向量库。没有 DATABASE_URL 时失败，不建 FAISS。

    返回已经装好检索的 RAG 和账号库。连接失败时抛出 ConnectionError，且不附带连接串。
    """
    # 缺少连接串就停。退回本地 FAISS 会读到未发布版本。
    database_url = require_env(DATABASE_URL_ENV)
    rag = _selected_rag()
    auth = open_auth()
    _attach_published(rag, auth, database_url)
    return rag, auth


def main() -> None:
    """装好已发布检索后在本机端口提供页面。闲置摘要在别的线程里扫。"""
    rag, auth = open_published_demo()
    DemoHandler.rag = rag
    DemoHandler.auth = auth
    # 不放进提问请求里等模型，所以下一问不会被这次压缩挡住。
    idle = start_idle_scan(
        DemoHandler.auth.db,
        summarize_with_qwen,
        on_alert=_alert_site_cost,
    )
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"已装入 {_chunk_count(rag)} 个检索块")
    print(f"http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        idle.stop.set()
        server.server_close()


def _attach_published(rag: ElderBrainRag, auth: AuthApp, database_url: str) -> None:
    # 父节随每次提问读到的已发布块现拼。启动时装本地清单会把旧正文固定到进程退出。
    store = PgChunkIndex(
        connect_postgres(database_url),
        lambda: published_index_version(auth.db),
    )
    store.ensure_schema()
    rag.retrieval = _published_search(store, rag, auth.db)


def _published_search(
    store: PgChunkIndex,
    rag: ElderBrainRag,
    db: AuthDb,
) -> PublishedHybridSearch:
    embeddings = rag.index.embeddings
    if embeddings is None:
        raise ValueError("嵌入客户端未配置")
    return PublishedHybridSearch(store, embeddings.embed_query, lambda: enabled_source_ids(db))


def _chunk_count(rag: ElderBrainRag) -> int:
    retrieval = rag.retrieval
    if not isinstance(retrieval, PublishedHybridSearch):
        raise ValueError("检索未接上已发布索引")
    return retrieval.visible_count()


def _alert_site_cost() -> None:
    # 费用已经提交。发信失败不能回滚，日志也不写邮局返回的正文。
    try:
        send_alert_mail(
            COST_ALERT_SUBJECT,
            COST_ALERT_BODY,
            deliver=DemoHandler.auth.alert_deliver,
        )
    except Exception as exc:
        sys.stderr.write(f"费用告警没有发出 {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
