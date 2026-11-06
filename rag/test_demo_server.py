import tempfile
import unittest
from pathlib import Path

from demo_server import web_file


class WebFileTests(unittest.TestCase):
    def test_serves_index_and_blocks_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / "web"
            root.mkdir()
            outside = base / "demo_server.py"
            outside.write_text("secret", encoding="utf-8")
            page = root / "index.html"
            page.write_text("ok", encoding="utf-8")
            self.assertEqual(web_file(root, "/"), page.resolve())
            self.assertEqual(web_file(root, "/index.html"), page.resolve())
            self.assertIsNone(web_file(root, "/../demo_server.py"))
            self.assertIsNone(web_file(root, "/%2e%2e/demo_server.py"))
            self.assertIsNone(web_file(root, "/%5c..%5cdemo_server.py"))
            self.assertIsNone(web_file(root, "/missing.css"))


if __name__ == "__main__":
    unittest.main()
