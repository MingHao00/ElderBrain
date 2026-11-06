"""用本机 MinerU 文字层解析英文 SRD，并整理字距。结果写到 parse/。"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from pdf_text import normalize_pdf_text

PARSE_URL = "http://127.0.0.1:16580/v1/parse/jobs"
POLL_SECONDS = 20
MAX_POLLS = 150
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "parse"


def _request(method: str, url: str, body: dict[str, object] | None = None) -> bytes:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{error.code} {detail}") from error


def _submit(pdf_path: Path) -> str:
    body = {
        "files": [{"source": {"type": "local", "path": str(pdf_path)}, "page_range": "all"}],
        "tier": "basic",
        "ocr_mode": "txt",
        "output_formats": ["markdown", "structured_content"],
    }
    created = json.loads(_request("POST", PARSE_URL, body))
    job_id = str(created["job_id"])
    print(f"submitted {pdf_path.name} {job_id}", flush=True)
    return job_id


def _wait(job_id: str) -> dict[str, object]:
    for _ in range(MAX_POLLS):
        job = json.loads(_request("GET", f"{PARSE_URL}/{job_id}"))
        status = str(job.get("status"))
        print(f"{job_id} {status} {job.get('progress')}", flush=True)
        if status in {"completed", "failed", "canceled", "partial"}:
            return job
        time.sleep(POLL_SECONDS)
    raise TimeoutError(job_id)


def _download(file_id: str, destination: Path) -> None:
    destination.write_bytes(_request("GET", f"http://127.0.0.1:16580/v1/files/{file_id}/content"))
    print(f"wrote {destination} ({destination.stat().st_size} bytes)", flush=True)


def _save(job: dict[str, object], stem: str) -> None:
    files = job.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError(f"no files in {job}")
    first = files[0]
    if not isinstance(first, dict) or first.get("status") != "completed":
        raise RuntimeError(f"parse failed: {first}")
    outputs = first.get("output_files")
    if not isinstance(outputs, dict):
        raise RuntimeError("missing output_files")
    markdown = outputs.get("markdown")
    structured = outputs.get("structured_content")
    if not isinstance(markdown, dict) or not isinstance(structured, dict):
        raise RuntimeError(f"missing artifacts: {outputs}")
    raw_path = OUTPUT_DIR / f"{stem}.md"
    _download(str(markdown["file_id"]), raw_path)
    cleaned = normalize_pdf_text(raw_path.read_text(encoding="utf-8"))
    clean_path = OUTPUT_DIR / f"{stem}.clean.md"
    clean_path.write_text(cleaned, encoding="utf-8")
    print(f"wrote {clean_path}", flush=True)
    _download(str(structured["file_id"]), OUTPUT_DIR / f"{stem}.structured.json")


def parse_one(pdf_path: Path, stem: str) -> None:
    _save(_wait(_submit(pdf_path)), stem)


def repair_saved(pdf_path: Path, stem: str) -> None:
    """用已经下载的文字层补属性表和粘连表格，不重新提交解析。"""
    from pdf_table_repair import load_pdf_plain, repair_glued_tables

    raw = (OUTPUT_DIR / f"{stem}.md").read_text(encoding="utf-8")
    cleaned = normalize_pdf_text(raw)
    repaired = repair_glued_tables(cleaned, load_pdf_plain(pdf_path))
    destination = OUTPUT_DIR / f"{stem}.clean.md"
    destination.write_text(repaired + "\n", encoding="utf-8")
    print(f"wrote {destination}", flush=True)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) == 3 and sys.argv[1] == "repair":
        pdf_name = "SRD_CC_v5.1.pdf" if sys.argv[2] == "src-srd51-en" else "SRD_CC_v5.2.1.pdf"
        repair_saved(REPO_ROOT / pdf_name, sys.argv[2])
        return
    if len(sys.argv) == 3:
        _save(_wait(sys.argv[1]), sys.argv[2])
        return
    pending = (
        (REPO_ROOT / "SRD_CC_v5.1.pdf", "src-srd51-en"),
        (REPO_ROOT / "SRD_CC_v5.2.1.pdf", "src-srd521-en"),
    )
    for pdf_path, stem in pending:
        if (OUTPUT_DIR / f"{stem}.clean.md").exists():
            print(f"skip {stem}", flush=True)
            continue
        parse_one(pdf_path, stem)


if __name__ == "__main__":
    main()
