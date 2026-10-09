import json
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from app.ollama.client import ModelNotInstalledError, OllamaClient
from app.storage.writer import save_raw_page
from app.pipeline import Pipeline
from app.window.extractor import Candidate, WindowExtractor
from app.window.rules import load_rules
from app.downloads.rules import load_download_rules
from tests.fixtures import GENERIC_QUESTION_HTML



class FakeOllama:
    def __init__(self, answer=None, exc=None):
        self.answer = answer
        self.exc = exc

    def chat_json(self, model, system_prompt, user_content):
        if self.exc:
            raise self.exc
        return self.answer


class OllamaTestHandler(BaseHTTPRequestHandler):
    mode = "missing"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/api/tags":
            body = {"models": []} if self.mode == "missing" else {"models": [{"name": "qwen3:4b"}]}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/chat":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            data = b'{"error":"model \'qwen3:4b\' not found, try pulling it first"}'
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()


class PipelineTests(unittest.TestCase):
    def test_pipeline_records_basic_task_classification(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=None,
                window_model="small",
                answer_model="large",
                use_answer_llm=False,
            )
            result = pipeline.process_raw_file(raw)
            self.assertEqual(result["status"], "clean_ready")
            self.assertEqual(result["task_classification"]["mode"], "basic")
            self.assertEqual(result["task_classification"]["response_mode"], "choice")

    def test_pipeline_records_sandbox_task_classification(self):
        sandbox_html = '''
        <div class="assessment-container">
          <article class="task-item" data-task-id="sandbox-1">
            <div class="task-question">
              <div class="question-text">
              <p>Create a workspace and invite a teammate.</p>
              </div>
            </div>
            <div class="interactive-sandbox-container">
              <div class="sandbox-content">
                <iframe class="sandbox-frame" src="https://sandbox.example.test/interactive/simulator" title="Simulateur de messagerie"></iframe>
              </div>
            </div>
            <form><fieldset class="task-response"><legend>Action requise</legend><input type="text" aria-label="Réponse"></fieldset></form>
          </article>
        </div>
        '''
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "sandbox.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(sandbox_html, encoding="utf-8")
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=None,
                window_model="small",
                answer_model="large",
                use_answer_llm=False,
            )
            result = pipeline.process_raw_file(raw, page_url="https://app.example.test/q")
            self.assertEqual(result["status"], "clean_ready")
            self.assertEqual(result["task_classification"]["mode"], "sandbox")
            self.assertEqual(len(result["task_classification"]["sandbox_frames"]), 1)

    def test_configured_selector_extracts_task_item_only(self):
        extractor = WindowExtractor(load_rules())
        result = extractor.extract(GENERIC_QUESTION_HTML)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.confidence, 1.0)
        self.assertIn('task-item', result.html)
        self.assertIn("Submit answer", result.html)
        self.assertNotIn("Report an issue with this task", result.html)
        self.assertNotIn("page-progress", result.html)


    def test_raw_filename_uses_timestamp_not_title(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            path = save_raw_page(base, 'Étrange / titre: प्रश्न?.html', '<html></html>')
            self.assertRegex(path.name, r"^\d{8}T\d{6}_\d{6}Z\.html$")
            self.assertNotIn('Étrange', path.name)
            self.assertNotIn('?', path.name)
            self.assertTrue(path.exists())

    def test_missing_model_is_typed(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), OllamaTestHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OllamaClient(f"http://127.0.0.1:{server.server_port}", timeout=2)
            with self.assertRaises(ModelNotInstalledError):
                client.chat_json("qwen3:4b", "system", "hello")
        finally:
            server.shutdown()
            server.server_close()

    def test_window_model_missing_uses_real_raw_path(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "20260929T193000_000000Z.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            class FakeWindowMissing(FakeOllama):
                pass
            fake = FakeOllama(exc=ModelNotInstalledError("missing model"))
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=fake,
                window_model="small",
                answer_model="large",
            )
            pipeline.missing_model_handler = lambda model, stage, resume_file: False
            extraction = WindowExtractor(load_rules()).extract(GENERIC_QUESTION_HTML)
            extraction.status = "uncertain"
            extraction.html = None
            extraction.reason = "ambiguous"
            extraction.candidates = [Candidate(
                index=0, tag_name="article", path="html>body>article", score=50.0,
                text="example question", html="<article>example</article>"
            )]
            pipeline.extractor.extract = lambda html: extraction
            with self.assertRaises(SystemExit):
                pipeline.process_raw_file(raw)
            reports = list((base / "data" / "errors").glob("*.json"))
            self.assertTrue(reports)
            payload = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["file"], raw.name)

    def test_answer_model_failure_keeps_clean_page(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")

            from app.ollama.client import ModelNotInstalledError
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=FakeOllama(exc=ModelNotInstalledError("missing model")),
                window_model="small",
                answer_model="large",
            )
            pipeline.missing_model_handler = lambda model, stage, resume_file: False
            with self.assertRaises(SystemExit):
                pipeline.process_raw_file(raw)
            self.assertTrue((base / "data" / "clean_window_pages" / "page.html").exists())
            self.assertFalse((base / "data" / "answer" / "page.html").exists())
            errors = list((base / "data" / "errors").glob("*.json"))
            self.assertEqual(len(errors), 1)
            payload = json.loads(errors[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["code"], "MODEL_NOT_INSTALLED")

    def test_answer_folders_contain_only_json_decision_files(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            fake = FakeOllama(answer={
                "status": "answer",
                "confidence": 0.98,
                "answer": "Enable two-factor authentication",
                "reason": "Choice 3",
            })
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=fake,
                window_model="small",
                answer_model="large",
            )
            pipeline.process_raw_file(raw)
            answer_files = list((base / "data" / "answer").iterdir())
            self.assertEqual([p.suffix for p in answer_files], [".json"])

    def test_model_answer_writes_json_to_answer(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            fake = FakeOllama(answer={
                "status": "answer",
                "confidence": 0.98,
                "answer": "Enable two-factor authentication",
                "reason": "The question asks what she should click to follow the account.",
            })
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=fake,
                window_model="small",
                answer_model="large",
            )
            result = pipeline.process_raw_file(raw)
            self.assertEqual(result["status"], "answer")
            self.assertTrue((base / "data" / "clean_window_pages" / "page.html").exists())
            self.assertFalse((base / "data" / "answer" / "page.html").exists())
            self.assertFalse((base / "data" / "answer" / "page.txt").exists())
            answer_json = base / "data" / "answer" / "page.json"
            self.assertTrue(answer_json.exists())
            payload = json.loads(answer_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["answer"], "Enable two-factor authentication")
            self.assertEqual(payload["status"], "answer")

    def test_model_answer_not_found_writes_json(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            fake = FakeOllama(answer={
                "status": "answer_not_found",
                "confidence": 0.31,
                "answer": None,
                "reason": "Not sufficiently certain.",
            })
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=fake,
                window_model="small",
                answer_model="large",
            )
            result = pipeline.process_raw_file(raw)
            self.assertEqual(result["status"], "answer_not_found")
            self.assertTrue((base / "data" / "clean_window_pages" / "page.html").exists())
            self.assertFalse((base / "data" / "answer_not_found" / "page.html").exists())
            self.assertFalse((base / "data" / "answer_not_found" / "page.txt").exists())
            answer_json = base / "data" / "answer_not_found" / "page.json"
            self.assertTrue(answer_json.exists())
            payload = json.loads(answer_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "answer_not_found")
            self.assertEqual(payload["reason"], "Not sufficiently certain.")


if __name__ == "__main__":
    unittest.main()

class CaptureOllama(FakeOllama):
    def __init__(self, answer=None, exc=None):
        super().__init__(answer=answer, exc=exc)
        self.calls = []
        self.last_metrics = {
            "total_duration": 100_000_000,
            "load_duration": 10_000_000,
            "prompt_eval_count": 100,
            "prompt_eval_duration": 20_000_000,
            "eval_count": 15,
            "eval_duration": 60_000_000,
            "client_wall_seconds": 0.11,
        }

    def chat_json(self, model, system_prompt, user_content, images=None):
        self.calls.append((model, system_prompt, user_content, images))
        return super().chat_json(model, system_prompt, user_content)


def _make_temp_page(base: Path):
    raw = base / "data" / "raw_pages" / "page.html"
    raw.parent.mkdir(parents=True)
    raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
    return raw


class DebuggingAndCompactTests(unittest.TestCase):
    def test_compact_representation_keeps_question_and_choices(self):
        from app.answer_representation import build_compact_representation
        result = build_compact_representation(GENERIC_QUESTION_HTML, filename="page.html", title="Example page", page_url="https://example.test")
        text = result["model_text"]
        self.assertIn("Which action best improves account security", text)
        self.assertIn("Enable two-factor authentication", text)
        self.assertIn("Disable security updates", text)
        self.assertNotIn("<svg", text)
        self.assertLess(len(text), len(GENERIC_QUESTION_HTML))

    def test_compact_answer_input_is_used_and_profile_is_saved(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = _make_temp_page(base)
            fake = CaptureOllama(answer={
                "status": "answer", "confidence": 0.98, "answer": "Enable two-factor authentication", "reason": "Choice 3"
            })
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=fake,
                window_model="small",
                answer_model="large",
            )
            result = pipeline.process_raw_file(raw)
            self.assertEqual(result["status"], "answer")
            self.assertEqual(len(fake.calls), 1)
            sent = fake.calls[0][2]
            sent_payload = json.loads(sent)
            self.assertEqual(sent_payload["input_mode"], "compact_question_representation")
            self.assertIn("Which action best improves account security", sent_payload["model_input"])
            profiles = list((base / "data" / "debug" / "ollama").glob("*.json"))
            self.assertTrue(profiles)
            profile = json.loads(profiles[0].read_text(encoding="utf-8"))
            self.assertEqual(profile["stage"], "answer_model")
            self.assertEqual(profile["metrics"]["prompt_tokens"], 100)
            self.assertEqual(profile["metrics"]["output_tokens"], 15)

    def test_full_answer_input_mode_is_available(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = _make_temp_page(base)
            fake = CaptureOllama(answer={
                "status": "answer", "confidence": 0.98, "answer": "Enable two-factor authentication", "reason": "Choice 3"
            })
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=fake,
                window_model="small",
                answer_model="large",
                answer_input_mode="full",
            )
            result = pipeline.process_raw_file(raw)
            self.assertEqual(result["status"], "answer")
            self.assertEqual(len(fake.calls), 1)
            sent_payload = json.loads(fake.calls[0][2])
            self.assertEqual(sent_payload["input_mode"], "full_clean_html")
            self.assertIn("task-item", sent_payload["model_input"])


    def test_vision_model_analyzes_captured_screenshot_before_answer_model(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = _make_temp_page(base)
            screenshot = base / "data" / "screenshots" / "screenshot.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pixels")

            class VisionAndAnswerOllama(CaptureOllama):
                def chat_json(self, model, system_prompt, user_content, images=None):
                    self.calls.append((model, system_prompt, user_content, images))
                    if model == "vision-small":
                        return {
                            "summary": "A visible question page.",
                            "observations": ["A choice control is visible."],
                            "confidence": 0.93,
                        }
                    return {
                        "status": "answer", "confidence": 0.96, "answer": "Enable two-factor authentication", "reason": "Choice 3"
                    }

            fake = VisionAndAnswerOllama(answer=None)
            pipeline = Pipeline(
                base_dir=base, extractor=WindowExtractor(load_rules()), ollama=fake,
                window_model="small", answer_model="large", vision_model="vision-small",
                ocr_enabled=False,
            )
            result = pipeline.process_raw_file(raw, screenshot_path=screenshot)
            self.assertEqual(result["status"], "answer")
            self.assertEqual(fake.calls[0][0], "vision-small")
            self.assertEqual(fake.calls[0][3], [screenshot])
            self.assertEqual(fake.calls[1][0], "large")
            payload = json.loads(fake.calls[1][2])
            self.assertEqual(payload["screenshot_evidence"]["visual_analysis"]["summary"], "A visible question page.")

    def test_screenshot_evidence_reaches_answer_model_without_vision_model(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = _make_temp_page(base)
            screenshot = base / "data" / "screenshots" / "screenshot.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pixels")
            fake = CaptureOllama(answer={
                "status": "answer", "confidence": 0.96, "answer": "Enable two-factor authentication", "reason": "Choice 3"
            })
            pipeline = Pipeline(
                base_dir=base, extractor=WindowExtractor(load_rules()), ollama=fake,
                window_model="small", answer_model="large",
                ocr_enabled=False, visual_analysis_enabled=False,
            )
            result = pipeline.process_raw_file(raw, screenshot_path=screenshot)
            self.assertEqual(result["status"], "answer")
            payload = json.loads(fake.calls[0][2])
            self.assertEqual(payload["screenshot_evidence"]["screenshot"]["status"], "present")
            self.assertEqual(fake.calls[0][0], "large")
    def test_attachment_evidence_is_passed_to_answer_model(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            attachment = base / "document.txt"
            attachment.write_text("supporting evidence", encoding="utf-8")

            class EvidenceOllama:
                last_metrics = {}
                def chat_json(self, model, system_prompt, user_content, images=None):
                    payload = json.loads(user_content)
                    evidence = payload["attachment_evidence"]
                    self.evidence = evidence
                    return {"status": "answer", "confidence": 0.99, "answer": "Enable two-factor authentication", "reason": "test"}

            fake = EvidenceOllama()
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
            self.assertEqual(fake.evidence["schema_version"], "2.0")
            self.assertIn("metadata", fake.evidence)
            self.assertIn("images", fake.evidence)
            self.assertIn("ocr", fake.evidence)
            self.assertIn("visual_analysis", fake.evidence)

class AutomaticDownloadPipelineTests(unittest.TestCase):
    @staticmethod
    def _make_docx_bytes(text: str) -> bytes:
        import io
        import zipfile
        xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>'''
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>")
            archive.writestr("_rels/.rels", "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>")
            archive.writestr("word/document.xml", xml)
        return buffer.getvalue()

    def test_pipeline_auto_downloads_same_origin_document(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from app.pipeline import Pipeline
        from app.window.extractor import WindowExtractor
        from app.window.rules import load_rules

        payload = self._make_docx_bytes("Auto downloaded answer")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                if self.path == "/download":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(404)
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                base = Path(td)
                raw = base / "data" / "raw_pages" / "page.html"
                raw.parent.mkdir(parents=True)
                html = GENERIC_QUESTION_HTML.replace(
                    "</article>",
                    '<a href="/download" download>Télécharger le document</a></article>',
                    1,
                )
                raw.write_text(html, encoding="utf-8")

                class FakeOllama:
                    last_metrics = {}
                    def chat_json(self, model, system_prompt, user_content):
                        self.user_content = user_content
                        return {
                            "status": "answer",
                            "confidence": 0.99,
                            "answer": "Auto download worked",
                            "reason": "The attachment was supplied safely.",
                        }

                fake = FakeOllama()
                pipeline = Pipeline(
                    base_dir=base,
                    extractor=WindowExtractor(load_rules()),
                    ollama=fake,
                    window_model="small",
                    answer_model="large",
                    download_rules={**load_download_rules(), "download_allow_private_ips": True},
                )
                url = f"http://127.0.0.1:{server.server_port}/question"
                result = pipeline.process_raw_file(raw, page_url=url)
                self.assertEqual(result["status"], "answer")
                self.assertIn("Auto downloaded answer", fake.user_content)
                quarantine = list((base / "data" / "downloads" / "quarantine").glob("*.bin"))
                self.assertEqual(quarantine, [])
                incoming = list((base / "data" / "downloads" / "incoming").glob("*"))
                self.assertEqual(incoming, [])
        finally:
            server.shutdown()
            server.server_close()

    def test_pipeline_does_not_treat_off_origin_link_as_download(self) -> None:
        from app.downloads.hints import find_download_candidates, select_download_candidate

        html = '''<article><a href="https://other.example/file.docx" download>Télécharger</a></article>'''
        candidates = find_download_candidates(html, "https://example.test/question", [".docx"])
        self.assertEqual(len(candidates), 1)
        self.assertIsNotNone(select_download_candidate(candidates))

class DownloadRetentionPolicyTests(unittest.TestCase):
    def _make_docx(self, path: Path, text: str) -> None:
        import io
        import zipfile
        xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'''
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>")
            archive.writestr("_rels/.rels", "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>")
            archive.writestr("word/document.xml", xml)
        path.write_bytes(buffer.getvalue())

    def _pipeline(self, base: Path, confidence: float, status: str = "answer"):
        from app.pipeline import Pipeline
        from app.window.extractor import WindowExtractor
        from app.window.rules import load_rules

        class FakeOllama:
            last_metrics = {}
            def chat_json(self, model, system_prompt, user_content):
                return {
                    "status": status,
                    "confidence": confidence,
                    "answer": "ok" if status == "answer" else None,
                    "reason": "test",
                }

        return Pipeline(
            base_dir=base,
            extractor=WindowExtractor(load_rules()),
            ollama=FakeOllama(),
            window_model="small",
            answer_model="large",
            download_rules={**load_download_rules()},
        )

    def _raw(self, base: Path) -> Path:
        from tests.test_pipeline import GENERIC_QUESTION_HTML
        raw = base / "data" / "raw_pages" / "page.html"
        raw.parent.mkdir(parents=True)
        raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
        return raw

    def test_confidence_above_point_nine_deletes_processed_binary(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = self._raw(base)
            attachment = base / "download"
            self._make_docx(attachment, "high confidence")
            pipeline = self._pipeline(base, 0.91)
            result = pipeline.process_raw_file(raw, attachment_path=attachment)
            self.assertEqual(result["status"], "answer")
            quarantine_bins = [p for p in (base / "data" / "downloads" / "quarantine").glob("*.bin")]
            self.assertEqual(quarantine_bins, [])
            decision = json.loads((base / "data" / "answer" / "page.json").read_text(encoding="utf-8"))
            self.assertEqual(decision["download_cleanup"]["action"], "deleted")

    def test_confidence_equal_to_point_nine_keeps_processed_binary(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = self._raw(base)
            attachment = base / "download"
            self._make_docx(attachment, "boundary")
            pipeline = self._pipeline(base, 0.90)
            result = pipeline.process_raw_file(raw, attachment_path=attachment)
            self.assertEqual(result["status"], "answer")
            quarantine_bins = [p for p in (base / "data" / "downloads" / "quarantine").glob("*.bin")]
            self.assertEqual(len(quarantine_bins), 1)
            decision = json.loads((base / "data" / "answer" / "page.json").read_text(encoding="utf-8"))
            self.assertEqual(decision["download_cleanup"]["action"], "kept")

    def test_answer_not_found_keeps_processed_binary_even_with_high_confidence(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = self._raw(base)
            attachment = base / "download"
            self._make_docx(attachment, "uncertain")
            pipeline = self._pipeline(base, 0.99, status="answer_not_found")
            result = pipeline.process_raw_file(raw, attachment_path=attachment)
            self.assertEqual(result["status"], "answer_not_found")
            quarantine_bins = [p for p in (base / "data" / "downloads" / "quarantine").glob("*.bin")]
            self.assertEqual(len(quarantine_bins), 1)
            decision = json.loads((base / "data" / "answer_not_found" / "page.json").read_text(encoding="utf-8"))
            self.assertEqual(decision["download_cleanup"]["action"], "kept")
            self.assertEqual(decision["download_cleanup"]["reason"], "answer_not_found")

if __name__ == "__main__":
    unittest.main()

class ScreenshotDependencyPromptTests(unittest.TestCase):
    def test_pipeline_offers_tesseract_when_screenshot_exists_and_ocr_is_enabled(self):
        from unittest.mock import patch
        from app.pipeline import Pipeline
        from app.window.extractor import WindowExtractor
        from app.window.rules import load_rules
        from app.dependency_installer import DependencyStatus

        html = GENERIC_QUESTION_HTML
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(html, encoding="utf-8")
            screenshot = base / "data" / "screenshots" / "page.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=None,
                window_model="small",
                answer_model="large",
                use_window_llm=False,
                use_answer_llm=False,
                ocr_enabled=True,
            )
            fake_status = DependencyStatus("tesseract", "Tesseract OCR", True, "installed")
            with patch("app.pipeline.find_tesseract", side_effect=[None, "C:/Tesseract-OCR/tesseract.exe"]), \
                patch("app.pipeline.load_dependency_config", return_value={"tesseract": {"languages": ["eng", "fra"]}}), \
                patch("app.pipeline.ensure_app", return_value=fake_status) as ensure_app, \
                patch("app.pipeline.ensure_tesseract_languages", return_value=(True, "ok")), \
                patch("app.pipeline.analyze_screenshot", return_value={"screenshot": {"status": "present"}, "ocr": {"status": "present"}, "visual_analysis": {"status": "unavailable"}}):
                result = pipeline.process_raw_file(raw, screenshot_path=screenshot)
            self.assertEqual(result["status"], "clean_ready")
            ensure_app.assert_called_once()
