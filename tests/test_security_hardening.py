from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.answer_representation import filter_attachment_evidence_for_ai
from app.downloads.detector import detect_file_type_bytes, inspect_zip_bytes
from app.downloads.downloader import _DNSPinningContext, is_trusted_download_url
from app.downloads.extract import extract_safe_text
from app.downloads.models import ValidationResult
from app.downloads.rules import load_download_rules
from app.downloads.security import validate_download
from app.pipeline_state import PipelineRunState


class SecurityHardeningTests(unittest.TestCase):
    def test_url_extension_absent_is_explicit_null_and_skips_consistency(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "download"
            path.write_text("hello", encoding="utf-8")
            result = validate_download(path, load_download_rules(), source_url="https://cdn.example.test/download?id=42")
            self.assertIsNone(result.url_extension)
            self.assertIsNone(result.url_match)
            self.assertTrue(result.accepted)

    def test_private_download_destination_is_rejected(self):
        with patch("app.downloads.downloader.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
            self.assertFalse(
                is_trusted_download_url(
                    "https://cdn.example.test/internal.docx",
                    "https://cdn.example.test/question",
                    same_origin_only=True,
                    allowed_hosts=set(),
                )
            )

    def test_dns_pinning_reuses_the_validated_address(self):
        import socket
        calls = []
        original = socket.getaddrinfo
        try:
            def fake(host, port, family=0, type=0, proto=0, flags=0):
                calls.append(host)
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]
            with patch("app.downloads.downloader.socket.getaddrinfo", side_effect=fake):
                context = _DNSPinningContext(allow_private_ips=False, allowed_ip_ranges=())
                with context as pinned:
                    pinned.validate_and_pin("example.test")
                    _ = socket.getaddrinfo("example.test", 443, type=socket.SOCK_STREAM)
            self.assertEqual(calls.count("example.test"), 1)
        finally:
            socket.getaddrinfo = original

    def test_url_extension_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "payload.pdf"
            path.write_bytes(b"hello")
            rules = {**load_download_rules(), "accepted_extensions": [".txt"]}
            result = validate_download(path, rules, source_url="https://cdn.example.test/payload.jpg")
            self.assertFalse(result.accepted)
            self.assertEqual(result.reason, "SECURITY_URL_TYPE_MISMATCH")
            self.assertFalse(result.url_match)

    def test_nested_archive_members_have_hash_and_parent_provenance(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as archive:
            archive.writestr("notes.txt", "nested text")
        inner_bytes = inner.getvalue()

        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w") as archive:
            archive.writestr("inner.zip", inner_bytes)
            archive.writestr("top.txt", "top text")
        outer_bytes = outer.getvalue()

        entries, _, _, _, nested = inspect_zip_bytes(outer_bytes, load_download_rules())
        inner_entry = next(entry for entry in entries if entry.name == "inner.zip")
        nested_entry = next(entry for entry in entries if entry.name.endswith("inner.zip!/notes.txt"))
        self.assertEqual(inner_entry.sha256, hashlib.sha256(inner_bytes).hexdigest())
        self.assertEqual(nested_entry.source_archive_sha256, inner_entry.sha256)
        self.assertEqual(nested_entry.sha256, hashlib.sha256(b"nested text").hexdigest())
        self.assertGreaterEqual(nested, 1)

    def test_windows_dangerous_member_name_is_rejected(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("CON/file.txt", "bad")
        with self.assertRaises(Exception) as ctx:
            detect_file_type_bytes(payload.getvalue(), load_download_rules())
        self.assertEqual(ctx.exception.code, "SECURITY_WINDOWS_RESERVED_FILENAME")

    def test_image_processing_produces_sanitized_png_without_external_viewer(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable in test environment")
        image_buffer = io.BytesIO()
        Image.new("RGB", (10, 12), (255, 255, 255)).save(image_buffer, format="JPEG")
        path = Path(tempfile.mkdtemp()) / "image.jpg"
        path.write_bytes(image_buffer.getvalue())
        rules = {**load_download_rules(), "ocr_enabled": False}
        result = validate_download(path, rules, source_url="https://cdn.example.test/image.jpg")
        self.assertTrue(result.accepted)
        content = extract_safe_text(path, result, max_chars=1000, rules=rules)
        self.assertEqual(content.metadata["image_analysis"][0]["source"], "downloaded_file")
        safe = content.safe_image_artifacts[0]
        self.assertTrue(safe.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(content.metadata["image_analysis"][0]["safe_artifact_sha256"], hashlib.sha256(safe).hexdigest())

    def test_external_document_relationship_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "external.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
                archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/remote" TargetMode="External"/></Relationships>')
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
            rules = load_download_rules()
            detection = detect_file_type_bytes(path.read_bytes(), rules)
            validation = ValidationResult(True, "accepted", detection, ".docx", ".docx")
            with self.assertRaises(Exception) as ctx:
                extract_safe_text(path, validation, max_chars=1000, rules=rules)
            self.assertEqual(ctx.exception.code, "DOCUMENT_EXTERNAL_RESOURCE_BLOCKED")

    def test_ai_evidence_filter_excludes_quarantine_and_hash_lineage(self):
        evidence = {
            "file": {
                "extension": ".docx",
                "detected_kind": "zip-container",
                "sha256": "secret",
                "quarantine_path": "C:/secret/file.bin",
                "url_extension": ".docx",
            },
            "security": {"status": "accepted", "debug_path": "C:/secret"},
            "text": {"status": "present", "content": "hello"},
        }
        filtered = filter_attachment_evidence_for_ai(evidence)
        encoded = json.dumps(filtered, ensure_ascii=False)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("quarantine_path", encoded)
        self.assertNotIn("debug_path", encoded)
        self.assertIn("hello", encoded)

    def test_pipeline_state_rejects_backward_transition_and_preserves_first_failure(self):
        state = PipelineRunState()
        state.enter("HASH")
        state.finish("HASH", status="SUCCESS")
        state.enter("TYPE_DETECTION")
        state.fail("TYPE_DETECTION", "TYPE_UNKNOWN", "unknown file signature")
        with self.assertRaises(RuntimeError):
            state.enter("HASH")
        payload = state.as_dict()
        self.assertEqual(payload["first_failure"]["error_code"], "TYPE_UNKNOWN")
        self.assertEqual(payload["transition_index"], 7)

    def test_choice_index_is_validated_against_visible_choices(self):
        # Pipeline-level answer-schema behavior is covered without invoking Ollama.
        from app.pipeline import Pipeline
        from app.window.extractor import WindowExtractor
        from app.window.rules import load_rules
        from tests.test_choice_answers import GENERIC_QUESTION_HTML, FakeOllama

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "choice-index.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=FakeOllama({"status": "answer", "confidence": 0.94, "choice_index": 3, "answer": "Enable two-factor authentication"}),
                window_model="small",
                answer_model="answer",
                use_window_llm=False,
                use_answer_llm=True,
            )
            result = pipeline.process_raw_file(raw)
            self.assertEqual(result["status"], "answer")
            self.assertEqual(result["decision"]["answer"], "Enable two-factor authentication")
            self.assertEqual(result["decision"]["validated_confidence"], 0.94)
            self.assertEqual(result["decision"]["model_choice_index"], 3)


if __name__ == "__main__":
    unittest.main()
