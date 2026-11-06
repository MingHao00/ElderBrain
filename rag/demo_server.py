"""本地演示页。静态文件和提问都走本机，密钥留在环境变量。"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from config import REPO_ROOT
from demo_answer import MAX_BODY_BYTES, present_answer, read_turn
from run_generation_experiment import _public_error
from system import ElderBrainRag, _selected_rag

WEB_DIR = REPO_ROOT / "web"
HOST = "127.0.0.1"
PORT = 8765
ASK_PATH = "/api/ask"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


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
    """一个本机页面。提问交给已经装好的 RAG。"""

    rag: ElderBrainRag

    def do_GET(self) -> None:
        found = web_file(WEB_DIR, urlparse(self.path).path)
        if found is None:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        kind = CONTENT_TYPES.get(found.suffix, "application/octet-stream")
        self._send(200, found.read_bytes(), kind)

    def do_POST(self) -> None:
        if urlparse(self.path).path != ASK_PATH:
            self._json(404, {"error": "没有这个接口"})
            return
        try:
            question, history = read_turn(self._body())
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        try:
            answer = present_answer(self.rag.ask(question, history))
        except Exception as exc:
            print(_public_error(exc))
            self._json(500, {"error": "回答失败"})
            return
        self._json(200, {"answer": answer})

    def _body(self) -> bytes:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise ValueError("请求不是 JSON") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("问题太长")
        return self.rfile.read(length)

    def _json(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    rag = _selected_rag()
    count = rag.build_knowledge_base()
    DemoHandler.rag = rag
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"已装入 {count} 个检索块")
    print(f"http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
