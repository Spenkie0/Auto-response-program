import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflow import _process_existing


class DummyPipeline:
    def __init__(self):
        self.calls = []

    def process_raw_file(self, path, *, page_url=None, title=None, attachment_path=None, screenshot_path=None):
        self.calls.append({
            "path": path,
            "page_url": page_url,
            "title": title,
            "attachment_path": attachment_path,
            "screenshot_path": screenshot_path,
        })


class WorkflowMetadataTests(unittest.TestCase):
    def _args(self, **kwargs):
        values = {
            "file": "page.html",
            "url": "",
            "title": "",
            "attachment": None,
        }
        values.update(kwargs)
        return argparse.Namespace(**values)

    def test_native_environment_metadata_is_used(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text("<html></html>", encoding="utf-8")
            pipeline = DummyPipeline()
            with patch.dict(os.environ, {
                "SCRAPPER_CAPTURE_URL": "https://example.test/?x=1&y=2",
                "SCRAPPER_CAPTURE_TITLE": 'Libre | Question 3 & 5',
            }, clear=False):
                _process_existing(self._args(), base, pipeline)
            self.assertEqual(pipeline.calls[0]["page_url"], "https://example.test/?x=1&y=2")
            self.assertEqual(pipeline.calls[0]["title"], 'Libre | Question 3 & 5')

    def test_explicit_cli_metadata_overrides_environment(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text("<html></html>", encoding="utf-8")
            pipeline = DummyPipeline()
            with patch.dict(os.environ, {
                "SCRAPPER_CAPTURE_URL": "https://env.example",
                "SCRAPPER_CAPTURE_TITLE": "Environment title",
            }, clear=False):
                _process_existing(self._args(url="https://cli.example", title="CLI title"), base, pipeline)
            self.assertEqual(pipeline.calls[0]["page_url"], "https://cli.example")
            self.assertEqual(pipeline.calls[0]["title"], "CLI title")

    def test_process_all_does_not_leak_capture_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw_dir = base / "data" / "raw_pages"
            raw_dir.mkdir(parents=True)
            (raw_dir / "one.html").write_text("<html></html>", encoding="utf-8")
            (raw_dir / "two.html").write_text("<html></html>", encoding="utf-8")
            pipeline = DummyPipeline()
            with patch.dict(os.environ, {
                "SCRAPPER_CAPTURE_URL": "https://env.example",
                "SCRAPPER_CAPTURE_TITLE": "Environment title",
            }, clear=False):
                _process_existing(self._args(file=None), base, pipeline)
            self.assertEqual(len(pipeline.calls), 2)
            self.assertTrue(all(call["page_url"] is None for call in pipeline.calls))
            self.assertTrue(all(call["title"] is None for call in pipeline.calls))


if __name__ == "__main__":
    unittest.main()
