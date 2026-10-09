from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
import io
import hashlib
from pathlib import Path
from unittest.mock import patch

from app.answer_representation import build_compact_representation
from app.downloads.detector import detect_file_type
from app.downloads.extract import extract_safe_text
from app.downloads.models import DownloadIngestionError
from app.downloads.downloader import is_trusted_download_url
from app.downloads.rules import load_download_rules
from app.downloads.security import validate_download

class DownloadSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_download_rules()

    @staticmethod
    def _make_docx(path: Path, text: str = "Hello from DOCX") -> None:
        xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>'''
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>")
            archive.writestr("_rels/.rels", "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>")
            archive.writestr("word/document.xml", xml)

    @staticmethod
    def _make_odt(path: Path, text: str = "Hello from ODT") -> None:
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
 <office:body><office:text><text:p>{text}</text:p></office:text></office:body>
</office:document-content>'''
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            archive.writestr("content.xml", xml)
            archive.writestr("meta.xml", "<office:document-meta xmlns:office=\"urn:oasis:names:tc:opendocument:xmlns:office:1.0\"/>")

    def test_extensionless_docx_detected_from_bytes_and_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "download"
            self._make_docx(path, "Question document")
            detection = detect_file_type(path, self.rules)
            self.assertEqual(detection.archive_kind, "docx")
            self.assertEqual(detection.canonical_extension, ".docx")
            self.assertTrue(any(entry.name == "word/document.xml" for entry in detection.archive_entries))
            validation = validate_download(path, self.rules)
            content = extract_safe_text(path, validation, 10000)
            self.assertIn("Question document", content.text)

    def test_extensionless_odt_detected_and_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "download"
            self._make_odt(path, "Question ODT")
            validation = validate_download(path, self.rules)
            content = extract_safe_text(path, validation, 10000, rules=self.rules)
            self.assertEqual(content.validation.effective_extension, ".odt")
            self.assertIn("Question ODT", content.text)


    def test_configured_jpeg_extension_is_accepted_and_exposed_as_image_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "image.jpg"
            from PIL import Image
            image = Image.new("RGB", (1, 1), (255, 255, 255))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG")
            path.write_bytes(buffer.getvalue())
            rules = {**load_download_rules(), "accepted_extensions": [".jpg"]}
            validation = validate_download(path, rules)
            self.assertTrue(validation.accepted)
            self.assertEqual(validation.effective_extension, ".jpg")
            self.assertEqual(validation.detection.kind, "jpeg")
            content = extract_safe_text(path, validation, 10000)
            self.assertEqual(content.text, "")
            self.assertEqual(content.evidence["images"]["status"], "present")

    def test_configured_source_extension_can_be_used_for_supported_text_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "notes.custom"
            path.write_text("hello custom text", encoding="utf-8")
            rules = {**load_download_rules(), "accepted_extensions": [".custom"]}
            validation = validate_download(path, rules)
            self.assertTrue(validation.accepted)
            self.assertEqual(validation.effective_extension, ".custom")
            content = extract_safe_text(path, validation, 10000)
            self.assertEqual(content.text, "hello custom text")

    def test_configured_unknown_zip_extension_is_not_hard_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "archive.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("payload.bin", b"binary")
            rules = {**load_download_rules(), "accepted_extensions": [".zip"]}
            validation = validate_download(path, rules)
            self.assertTrue(validation.accepted)
            self.assertEqual(validation.effective_extension, ".zip")
            self.assertEqual(validation.detection.kind, "zip-container")
            self.assertIsNone(validation.detection.archive_kind)

    def test_missing_download_config_allows_no_extensions_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "download_rules.json"
            rules = load_download_rules(str(missing))
            self.assertEqual(rules.get("accepted_extensions"), [])

    def test_disallowed_binary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "unknown"
            path.write_bytes(b"MZ" + b"\x00" * 100)
            validation = validate_download(path, self.rules)
            self.assertFalse(validation.accepted)
            self.assertEqual(validation.detection.kind, "pe")

    def test_unknown_zip_is_rejected_after_listing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "archive_without_extension"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("readme.txt", "hello")
            detection = detect_file_type(path, self.rules)
            self.assertEqual(detection.kind, "zip-container")
            self.assertIsNone(detection.archive_kind)
            self.assertEqual(detection.archive_entries[0].name, "readme.txt")
            validation = validate_download(path, self.rules)
            self.assertFalse(validation.accepted)

    def test_archive_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../payload.txt", "danger")
            with self.assertRaises(DownloadIngestionError) as ctx:
                detect_file_type(path, self.rules)
            self.assertEqual(ctx.exception.code, "ARCHIVE_UNSAFE_PATH")

    def test_docker_quarantine_preserves_hash_named_artifact_and_source_bytes(self) -> None:
        from app.downloads.workflow import process_download
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "downloaded file.txt"
            source.write_text("safe text", encoding="utf-8")
            result = process_download(source, base, rules=self.rules)
            digest = hashlib.sha256(b"safe text").hexdigest()
            self.assertEqual(result.content.validation.file_sha256, digest)
            self.assertTrue(Path(result.content.metadata["quarantine_path"]).exists())
            self.assertTrue(source.exists())

    def test_compact_representation_contains_attachment_text(self) -> None:
        html = "<article class='task-item'><p>What is the answer?</p><input type='radio' value='1'><label>One</label></article>"
        result = build_compact_representation(
            html,
            filename="page.html",
            attachment_text="The downloaded document says the answer is TWO.",
            attachment_metadata={"effective_extension": ".docx"},
            max_text_chars=10000,
        )
        self.assertIn("The downloaded document says the answer is TWO.", result["model_text"])
        self.assertIn(".docx", result["model_text"])

    def test_task_is_explicit_and_attachment_xml_metadata_is_removed(self) -> None:
        html = """
        <article class="task-item">
          <div class="task-question">
            <div class="question-instructions">
              <div class="question-text">
                <p>Remplacez le mot sed par le mot mais dans tout le texte.</p>
                <p>Combien de caractères contient le document suite à cette modification (en incluant les espaces) ?</p>
              </div>
            </div>
          </div>
          <fieldset class="task-response"><legend class="response-instructions">Saisissez une valeur.</legend></fieldset>
        </article>
        """
        result = build_compact_representation(
            html,
            filename="page.html",
            attachment_text="sed sed sed",
            attachment_metadata={
                "effective_extension": ".docx",
                "archive_member_previews": {"word/document.xml": "<w:document>XML noise</w:document>"},
                "processed_path": "C:/sensitive/path.docx",
            },
            max_text_chars=4000,
        )
        payload = json.loads(result["model_text"])
        self.assertTrue(payload["task"]["exact_task"].startswith("Remplacez le mot sed"))
        self.assertIn("Combien de caractères", payload["task"]["exact_task"])
        self.assertNotIn("XML noise", result["model_text"])
        self.assertNotIn("sensitive/path.docx", result["model_text"])

    def test_large_attachment_cannot_evict_exact_task(self) -> None:
        html = """
        <article class="task-item">
          <div class="task-question">
            <div class="question-instructions">
              <div class="question-text">
                <p>Remplacez le mot sed par le mot mais dans tout le texte.</p>
                <p>Combien de caractères contient le document suite à cette modification (en incluant les espaces) ?</p>
              </div>
            </div>
          </div>
        </article>
        """
        huge_attachment = "X" * 20000
        result = build_compact_representation(
            html,
            filename="page.html",
            attachment_text=huge_attachment,
            attachment_metadata={"effective_extension": ".docx"},
            max_text_chars=2000,
        )
        payload = json.loads(result["model_text"])
        self.assertIn("Remplacez le mot sed", payload["task"]["exact_task"])
        self.assertIn("Combien de caractères", payload["task"]["exact_task"])
        self.assertLessEqual(len(result["model_text"]), 2000)


if __name__ == "__main__":
    unittest.main()

class PipelineAttachmentTests(unittest.TestCase):
    @staticmethod
    def _make_docx(path: Path, text: str) -> None:
        xml = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>")
            archive.writestr("_rels/.rels", "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>")
            archive.writestr("word/document.xml", xml)

    def test_pipeline_passes_safe_attachment_text_to_answer_model(self) -> None:
        from app.pipeline import Pipeline
        from app.window.extractor import WindowExtractor
        from app.window.rules import load_rules
        from tests.fixtures import GENERIC_QUESTION_HTML

        class RecordingOllama:
            def __init__(self) -> None:
                self.calls = []
                self.last_metrics = {}

            def chat_json(self, model, system_prompt, user_content):
                self.calls.append((model, system_prompt, user_content))
                return {
                    "status": "answer",
                    "confidence": 0.99,
                    "answer": "The document answer",
                    "reason": "Supported by the attachment text.",
                }

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw_dir = base / "data" / "raw_pages"
            raw_dir.mkdir(parents=True)
            raw = raw_dir / "page.html"
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            attachment = base / "download"
            self._make_docx(attachment, "The document contains the required answer.")
            fake = RecordingOllama()
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=fake,
                window_model="small",
                answer_model="large",
                download_rules={**load_download_rules()},
            )
            result = pipeline.process_raw_file(raw, attachment_path=attachment)
            self.assertEqual(result["status"], "answer")
            self.assertEqual(len(fake.calls), 1)
            user_content = fake.calls[0][2]
            self.assertIn("The document contains the required answer.", user_content)
            metadata = list((base / "data" / "downloads" / "metadata").glob("*.json"))
            self.assertEqual(len(metadata), 1)
            quarantine = list((base / "data" / "downloads" / "quarantine").glob("*.bin"))
            self.assertEqual(quarantine, [])

class DownloadWorkflowTests(unittest.TestCase):
    @staticmethod
    def _make_docx(path: Path, text: str = "Workflow document") -> None:
        xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>'''
        content_types = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'''
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>")
            archive.writestr("word/document.xml", xml)

    def test_extensionless_docx_is_renamed_after_verified_detection(self) -> None:
        from app.downloads.workflow import process_download

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "download"
            self._make_docx(source, "Safe workflow text")
            result = process_download(source, base, rules=load_download_rules())
            self.assertEqual(result.content.validation.effective_extension, ".docx")
            self.assertIsNone(result.processed_path)
            self.assertTrue(Path(result.quarantine_path).exists())
            self.assertIn("Safe workflow text", result.content.text)

    def test_rejected_binary_is_moved_out_of_quarantine(self) -> None:
        from app.downloads.workflow import DownloadWorkflowError, process_download

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "payload"
            source.write_bytes(b"MZ" + b"\x00" * 32)
            with self.assertRaises(DownloadWorkflowError) as ctx:
                process_download(source, base, rules={**load_download_rules()})
            self.assertIsNotNone(ctx.exception.quarantine_path)
            self.assertTrue(ctx.exception.quarantine_path.exists())

    def test_zip_inspection_includes_bounded_text_preview(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "unknown.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("notes.txt", "hello from inside the archive")
            detection = detect_file_type(path, load_download_rules())
            self.assertIn("notes.txt", detection.archive_member_previews)
            self.assertIn("hello from inside", detection.archive_member_previews["notes.txt"])

    def test_macro_enabled_docx_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "document.docx"
            content_types = '''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>'''
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", content_types)
                archive.writestr("word/document.xml", "<doc/>")
                archive.writestr("word/vbaProject.bin", b"MZ" + b"\x00" * 20)
            validation = validate_download(path, load_download_rules())
            self.assertFalse(validation.accepted)
            self.assertEqual(validation.detection.archive_kind, "docx-macro")


class DownloadHostPolicyTests(unittest.TestCase):
    def test_same_origin_download_is_trusted(self) -> None:
        with patch("app.downloads.downloader.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            self.assertTrue(
                is_trusted_download_url(
                    "https://app.example.test/files/question.docx",
                    "https://app.example.test/question",
                    same_origin_only=True,
                    allowed_hosts=set(),
                )
            )

    def test_configured_cross_origin_cdn_host_is_trusted(self) -> None:
        with patch("app.downloads.downloader.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            self.assertTrue(
                is_trusted_download_url(
                    "https://cdn.example.test/abc/example.docx",
                    "https://app.example.test/question",
                    same_origin_only=True,
                    allowed_hosts={"cdn.example.test"},
                )
            )

    def test_untrusted_cross_origin_download_is_rejected(self) -> None:
        self.assertFalse(
            is_trusted_download_url(
                "https://evil.example/abc/example.docx",
                "https://app.example.test/question",
                same_origin_only=True,
                allowed_hosts={"cdn.example.test"},
            )
        )

class DownloadDetectionTests(unittest.TestCase):
    def test_download_candidate_is_found_and_ranked(self) -> None:
        from app.downloads.hints import find_download_candidates, select_download_candidate

        html = '''<article class="task-item">
          <p>Téléchargez le document.</p>
          <a class="download" href="/files/question" download>Télécharger</a>
        </article>'''
        candidates = find_download_candidates(
            html,
            "https://example.test/question",
            [".docx", ".odt", ".txt", ".csv"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].url, "https://example.test/files/question")
        self.assertIsNotNone(select_download_candidate(candidates))

    def test_ambiguous_download_candidates_are_not_auto_selected(self) -> None:
        from app.downloads.hints import find_download_candidates, select_download_candidate

        html = '''<article>
          <a href="/one.docx">Télécharger</a>
          <a href="/two.docx">Télécharger</a>
        </article>'''
        candidates = find_download_candidates(html, "https://example.test/q", [".docx"])
        self.assertEqual(len(candidates), 2)
        self.assertIsNone(select_download_candidate(candidates))

    def test_single_accepted_extension_link_is_auto_selected(self) -> None:
        from app.downloads.hints import find_download_candidates, select_download_candidate

        html = '<article><a href="/files/question.docx">Document</a></article>'
        candidates = find_download_candidates(html, "https://example.test/q", [".docx"])
        self.assertEqual(len(candidates), 1)
        self.assertIsNotNone(select_download_candidate(candidates))

    def test_plain_file_and_document_mentions_are_not_download_signals(self) -> None:
        from app.downloads.hints import page_may_require_download

        html = """
        <article class="task-item">
          <p>Jérôme recense régulièrement les myriapodes qui vivent près de son village.</p>
          <p>Il a un tableau contenant les nombres de myriapodes de chaque espèce.</p>
          <p>Il a déjà préparé deux fichiers : l’un au format xlsx, l’autre au format ods.</p>
          <input id="qroc_input" type="text" name="f32201603">
        </article>
        """
        self.assertFalse(page_may_require_download(html))

    def test_upload_file_input_is_not_a_download_signal(self) -> None:
        from app.downloads.hints import page_may_require_download

        html = '<label>Choisissez un fichier<input type="file"></label>'
        self.assertFalse(page_may_require_download(html))

    def test_explicit_download_prose_is_a_download_signal(self) -> None:
        from app.downloads.hints import page_may_require_download

        html = '<p>Téléchargez le document puis calculez le nombre de mots.</p>'
        self.assertTrue(page_may_require_download(html))

    def test_download_button_without_url_is_a_download_signal(self) -> None:
        from app.downloads.hints import page_may_require_download

        html = '<button type="button">Télécharger le fichier</button>'
        self.assertTrue(page_may_require_download(html))


class EvidenceSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_download_rules()

    def test_empty_evidence_has_stable_sections_and_null_defaults(self):
        from app.downloads.evidence import empty_evidence

        evidence = empty_evidence()
        for name in (
            "metadata", "structure", "text", "tables", "images",
            "ocr", "visual_analysis", "embedded_content", "security",
        ):
            self.assertIn(name, evidence)
            section = evidence[name]
            self.assertEqual(section["status"], "non-existent")
            for key, value in section.items():
                if key != "status":
                    self.assertIsNone(value)

    def test_evidence_is_persisted_in_fixed_location(self):
        from app.downloads.workflow import process_download

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "notes.txt"
            source.write_text("hello", encoding="utf-8")
            result = process_download(
                source,
                base,
                rules={**load_download_rules()},
            )
            evidence_path = Path(result.content.metadata["evidence_path"])
            self.assertTrue(evidence_path.exists())
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "2.0")
            self.assertEqual(payload["text"]["status"], "present")
            self.assertEqual(payload["images"]["status"], "non-existent")
            self.assertIsNone(payload["images"]["count"])

    def test_docx_embedded_image_is_reflected_in_evidence_schema(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "document.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>")
                archive.writestr("_rels/.rels", "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>")
                archive.writestr("word/document.xml", "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>")
                from PIL import Image
                image = Image.new("RGB", (1, 1), (255, 0, 0))
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                archive.writestr("word/media/image1.png", buffer.getvalue())
            validation = validate_download(path, self.rules)
            content = extract_safe_text(path, validation, 10000)
            self.assertEqual(content.evidence["images"]["status"], "present")
            self.assertEqual(content.evidence["images"]["count"], 1)
            self.assertIn(content.evidence["ocr"]["status"], {"present", "empty", "unavailable", "error", "timeout"})
            self.assertEqual(content.evidence["visual_analysis"]["status"], "non-existent")


    def test_jpeg_and_jpg_aliases_are_both_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            from PIL import Image
            image = Image.new("RGB", (1, 1), (255, 255, 255))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG")
            payload = buffer.getvalue()
            for suffix in (".jpg", ".jpeg"):
                path = base / f"image{suffix}"
                path.write_bytes(payload)
                rules = {**self.rules, "accepted_extensions": [".jpeg"], "extension_aliases": {".jpg": [".jpeg"]}}
                validation = validate_download(path, rules)
                self.assertTrue(validation.accepted, validation.reason)
                self.assertEqual(validation.detection.kind, "jpeg")
                self.assertEqual(validation.detection.canonical_extension, ".jpg")
                content = extract_safe_text(path, validation, 10000)
                self.assertEqual(content.text, "")
                self.assertEqual(content.evidence["images"]["status"], "present")

    def test_common_image_signatures_are_detected(self) -> None:
        samples = {
            ".png": (b"\x89PNG\r\n\x1a\n", "png"),
            ".gif": (b"GIF89a", "gif"),
            ".bmp": (b"BM", "bmp"),
            ".tif": (b"II*\x00", "tiff"),
            ".tiff": (b"MM\x00*", "tiff"),
            ".ico": (b"\x00\x00\x01\x00", "ico"),
            ".webp": (b"RIFF\x00\x00\x00\x00WEBP", "webp"),
            ".svg": (b"<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>", "svg"),
        }
        for suffix, (signature, kind) in samples.items():
            with self.subTest(suffix=suffix):
                with tempfile.TemporaryDirectory() as td:
                    path = Path(td) / f"image{suffix}"
                    path.write_bytes(signature if suffix == ".svg" else signature + b"\x00" * 32)
                    detection = detect_file_type(path, self.rules)
                    self.assertEqual(detection.kind, kind)
                    self.assertIsNotNone(detection.canonical_extension)

    def test_common_iso_bmff_image_signatures_are_detected(self) -> None:
        for brand, kind, extension in ((b"heic", "heif", ".heif"), (b"avif", "avif", ".avif")):
            with self.subTest(brand=brand):
                with tempfile.TemporaryDirectory() as td:
                    path = Path(td) / "image"
                    path.write_bytes(b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 32)
                    detection = detect_file_type(path, self.rules)
                    self.assertEqual(detection.kind, kind)
                    self.assertEqual(detection.canonical_extension, extension)

    def test_unknown_binary_cannot_be_accepted_by_filename_extension(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fake.jpg"
            path.write_bytes(b"\x01\x02\x03" + b"\x00" * 32)
            rules = {**self.rules, "accepted_extensions": [".jpg"]}
            validation = validate_download(path, rules)
            self.assertFalse(validation.accepted)
            self.assertEqual(validation.detection.kind, "unknown-binary")

    def test_jpeg_signature_is_detected_as_an_image(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "image.jpg"
            # Minimal JPEG/JFIF header plus an end marker for signature detection.
            path.write_bytes(bytes.fromhex("ffd8ffe000104a46494600010101006000600000ffd9"))
            detection = detect_file_type(path, load_download_rules())
            self.assertEqual(detection.kind, "jpeg")
            self.assertEqual(detection.canonical_extension, ".jpg")
            self.assertEqual(detection.mime_type, "image/jpeg")
