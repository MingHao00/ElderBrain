"""本地演示页。静态文件和提问都走本机，密钥留在环境变量。"""

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from audit_log import UNKNOWN_USER_ID, record_rejection, write_quietly
from auth_app import AuthApp, open_auth
from auth_constants import (
    ERR_ANSWER_FAILED,
    HTTP_BAD_REQUEST,
    HTTP_UNPROCESSABLE,
    PATH_ASK,
    PATH_QUERY,
    QUESTION_TOO_LONG,
)
from auth_db import AuthDb
from auth_http import Incoming, Outgoing, json_error
from auth_routes import route_api
from config import REPO_ROOT
from demo_answer import MAX_BODY_BYTES, present_answer
from run_generation_experiment import _public_error
from system import ElderBrainRag, _selected_rag

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

    def _answer(self, question: str, history: list[dict[str, str]]) -> str:
        return present_answer(self.rag.ask(question, history))

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


def main() -> None:
    """装好检索块后在本机端口提供页面。"""
    rag = _selected_rag()
    count = rag.build_knowledge_base()
    DemoHandler.rag = rag
    DemoHandler.auth = open_auth()
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"已装入 {count} 个检索块")
    print(f"http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
