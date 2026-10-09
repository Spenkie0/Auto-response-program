import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.pipeline import Pipeline
from app.vision import analyze_screenshot, run_ocr, run_visual_analysis
from app.window.extractor import WindowExtractor
from app.window.rules import load_rules

# 1x1 transparent PNG; enough for signature/dimension/evidence tests.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeVisionOllama:
    def __init__(self):
        self.calls = []
        self.last_metrics = {}

    def chat_json(self, model, system_prompt, user_content, images=None):
        self.calls.append({
            "model": model,
            "system": system_prompt,
            "user": user_content,
            "images": images,
        })
        self.last_metrics = {
            "total_duration": 1000000,
            "load_duration": 100000,
            "prompt_eval_count": 10,
            "prompt_eval_duration": 100000,
            "eval_count": 10,
            "eval_duration": 100000,
            "client_wall_seconds": 0.2,
        }
        return {
            "summary": "A page with a visible question and controls.",
            "observations": ["A button is visible."],
            "confidence": 0.91,
        }


class FakeAnswerOllama:
    def __init__(self):
        self.calls = []
        self.last_metrics = {}

    def chat_json(self, model, system_prompt, user_content):
        self.calls.append({"model": model, "system": system_prompt, "user": user_content})
        return {
            "status": "answer",
            "confidence": 0.95,
            "answer": "Test answer",
            "reason": "Sufficient evidence.",
        }


class VisionTests(unittest.TestCase):
    def test_analyze_screenshot_keeps_stable_schema_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            screenshot = base / "data" / "screenshots" / "capture.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(PNG_1X1)

            with patch("app.vision.run_ocr", return_value={
                "status": "present", "engine": "tesseract", "language": "fra+eng",
                "text": "Bonjour", "character_count": 7,
            }), patch("app.vision.run_visual_analysis", return_value={
                "status": "present", "model": "vision:test", "summary": "A button.",
                "observations": ["A button is visible."], "confidence": 0.9,
            }):
                evidence = analyze_screenshot(
                    base,
                    screenshot,
                    ollama=FakeVisionOllama(),
                    vision_model="vision:test",
                )

            self.assertEqual(evidence["screenshot"]["status"], "present")
            for section in ("screenshot", "ocr", "visual_analysis"):
                self.assertIn(section, evidence)
            self.assertEqual(evidence["ocr"]["text"], "Bonjour")
            self.assertEqual(evidence["visual_analysis"]["summary"], "A button.")
            saved = base / "data" / "screenshots" / "evidence" / "capture.json"
            self.assertTrue(saved.exists())
            stored = json.loads(saved.read_text(encoding="utf-8"))
            self.assertIn("ocr", stored)
            self.assertIn("visual_analysis", stored)

    def test_analyze_screenshot_without_vision_model_marks_visual_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            screenshot = base / "capture.png"
            screenshot.write_bytes(PNG_1X1)
            with patch("app.vision.run_ocr", return_value={
                "status": "present", "engine": "tesseract", "language": "eng",
                "text": None, "character_count": 0,
            }):
                evidence = analyze_screenshot(base, screenshot, vision_model="")
            self.assertEqual(evidence["visual_analysis"]["status"], "unavailable")
            self.assertIsNone(evidence["visual_analysis"]["summary"])

    def test_run_ocr_parses_tesseract_output(self):
        with tempfile.TemporaryDirectory() as td:
            screenshot = Path(td) / "capture.png"
            screenshot.write_bytes(PNG_1X1)
            completed = type("Completed", (), {"returncode": 0, "stdout": "Bonjour\n\n"})()
            list_langs = type("Completed", (), {"returncode": 0, "stdout": "List of available languages in:\neng\nfra\nosd\n"})()
            with patch("app.vision.subprocess.run", side_effect=[list_langs, completed]) as mocked:
                result = run_ocr(screenshot, language="fra+eng")
            self.assertEqual(result["status"], "present")
            self.assertEqual(result["text"], "Bonjour")
            self.assertEqual(result["language"], "fra+eng")
            self.assertEqual(mocked.call_count, 2)

    def test_run_ocr_uses_managed_tessdata_dir_when_configured(self):
        with tempfile.TemporaryDirectory() as td:
            screenshot = Path(td) / "capture.png"
            screenshot.write_bytes(PNG_1X1)
            tessdata = Path(td) / "data" / "tesseract"
            tessdata.mkdir(parents=True)
            list_langs = type("Completed", (), {"returncode": 0, "stdout": "List of available languages in x:\neng\nfra\n"})()
            completed = type("Completed", (), {"returncode": 0, "stdout": "Bonjour\n"})()
            with patch("app.vision.subprocess.run", side_effect=[list_langs, completed]) as mocked:
                result = run_ocr(screenshot, language="fra+eng", tessdata_base_dir=tessdata)
            self.assertEqual(result["status"], "present")
            self.assertIn("--tessdata-dir", mocked.call_args_list[0].args[0])
            self.assertIn("--tessdata-dir", mocked.call_args_list[1].args[0])
            self.assertIn(str(tessdata), mocked.call_args_list[0].args[0])

    def test_run_ocr_normalizes_nested_tessdata_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            screenshot = Path(td) / "capture.png"
            screenshot.write_bytes(PNG_1X1)
            tessdata = Path(td) / "data" / "tesseract"
            tessdata.mkdir(parents=True)
            list_langs = type("Completed", (), {"returncode": 0, "stdout": "List of available languages in x:\ntessdata/eng\ntessdata\\fra\n"})()
            completed = type("Completed", (), {"returncode": 0, "stdout": "Bonjour\n"})()
            with patch("app.vision.subprocess.run", side_effect=[list_langs, completed]):
                result = run_ocr(screenshot, language="fra+eng", tessdata_base_dir=tessdata)
            self.assertEqual(result["status"], "present")
            self.assertEqual(result["language"], "fra+eng")

    def test_run_ocr_marks_unavailable_when_tesseract_missing(self):
        with tempfile.TemporaryDirectory() as td:
            screenshot = Path(td) / "capture.png"
            screenshot.write_bytes(PNG_1X1)
            with patch("app.vision.subprocess.run", side_effect=FileNotFoundError):
                result = run_ocr(screenshot)
            self.assertEqual(result["status"], "unavailable")

    def test_run_visual_analysis_passes_screenshot_to_ollama(self):
        with tempfile.TemporaryDirectory() as td:
            screenshot = Path(td) / "capture.png"
            screenshot.write_bytes(PNG_1X1)
            fake = FakeVisionOllama()
            result = run_visual_analysis(screenshot, ollama=fake, model="vision:test")
            self.assertEqual(result["status"], "present")
            self.assertEqual(fake.calls[0]["model"], "vision:test")
            self.assertEqual(fake.calls[0]["images"], [screenshot])

    def test_answer_model_receives_screenshot_evidence(self):
        html = """
        <div class="assessment-container assessment-container--triggered">
          <article class="task-item">
            <div class="task-question">
              <div class="question-text"><p>Quelle est la réponse ?</p></div>
            </div>
            <form><input type="text" class="task-response__proposal"></form>
          </article>
        </div>
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "page.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(html, encoding="utf-8")
            fake = FakeAnswerOllama()
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=fake,
                window_model="small",
                answer_model="answer:test",
                use_window_llm=False,
                use_answer_llm=True,
            )
            result = pipeline._answer(
                html,
                raw,
                page_url="https://example.test",
                title="Question",
                screenshot_evidence={
                    "schema_version": "1.0",
                    "screenshot": {"status": "present", "path": "capture.png", "width": 1, "height": 1},
                    "ocr": {"status": "present", "engine": "tesseract", "language": "fra", "text": "Bonjour", "character_count": 7},
                    "visual_analysis": {"status": "present", "model": "vision:test", "summary": "A button.", "observations": ["button"], "confidence": 0.9},
                },
            )
            self.assertEqual(result["status"], "answer")
            payload = json.loads(fake.calls[0]["user"])
            self.assertEqual(payload["screenshot_evidence"]["ocr"]["text"], "Bonjour")
            self.assertEqual(payload["screenshot_evidence"]["visual_analysis"]["summary"], "A button.")


if __name__ == "__main__":
    unittest.main()
