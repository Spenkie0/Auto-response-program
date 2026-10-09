import json
import tempfile
import unittest
from pathlib import Path

from app.config import load_model_settings, load_vision_settings


class ModelConfigTests(unittest.TestCase):
    def test_loads_models_from_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_dir = base / "config"
            config_dir.mkdir()
            (config_dir / "model_settings.json").write_text(
                json.dumps({"window_verifier_model": "tiny-window", "answer_model": "strong-answer"}),
                encoding="utf-8",
            )
            settings = load_model_settings(base)
            self.assertEqual(settings["window_verifier_model"], "tiny-window")
            self.assertEqual(settings["answer_model"], "strong-answer")

    def test_invalid_config_uses_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_dir = base / "config"
            config_dir.mkdir()
            (config_dir / "model_settings.json").write_text("not-json", encoding="utf-8")
            settings = load_model_settings(base)
            self.assertEqual(settings["window_verifier_model"], "qwen3:4b")
            self.assertEqual(settings["answer_model"], "qwen3:8b")

    def test_loads_vision_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_dir = base / "config"
            config_dir.mkdir()
            (config_dir / "vision_settings.json").write_text(
                json.dumps({
                    "ocr_enabled": False,
                    "ocr_executable": "C:/tesseract/tesseract.exe",
                    "ocr_language": "eng",
                    "visual_analysis_enabled": False,
                }),
                encoding="utf-8",
            )
            settings = load_vision_settings(base)
            self.assertFalse(settings["ocr_enabled"])
            self.assertEqual(settings["ocr_executable"], "C:/tesseract/tesseract.exe")
            self.assertEqual(settings["ocr_language"], "eng")
            self.assertFalse(settings["visual_analysis_enabled"])

    def test_invalid_vision_config_uses_safe_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_dir = base / "config"
            config_dir.mkdir()
            (config_dir / "vision_settings.json").write_text("not-json", encoding="utf-8")
            settings = load_vision_settings(base)
            self.assertTrue(settings["ocr_enabled"])
            self.assertEqual(settings["ocr_executable"], "tesseract")
            self.assertEqual(settings["ocr_language"], "fra+eng")
            self.assertTrue(settings["visual_analysis_enabled"])


if __name__ == "__main__":
    unittest.main()
