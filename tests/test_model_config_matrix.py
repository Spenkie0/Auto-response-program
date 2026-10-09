import json
import tempfile
import unittest
from pathlib import Path

from app.config import load_model_settings


class ModelConfigMatrixTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            settings = load_model_settings(Path(td))
        self.assertEqual(settings["window_verifier_model"], "qwen3:4b")
        self.assertEqual(settings["answer_model"], "qwen3:8b")

    def test_invalid_json_returns_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "config").mkdir()
            (base / "config" / "model_settings.json").write_text("{not-json", encoding="utf-8")
            settings = load_model_settings(base)
        self.assertEqual(settings["answer_model"], "qwen3:8b")

    def test_non_object_json_returns_defaults(self):
        for raw in ["[]", "null", '"qwen3:8b"', "42", "true"]:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                (base / "config").mkdir()
                (base / "config" / "model_settings.json").write_text(raw, encoding="utf-8")
                settings = load_model_settings(base)
                self.assertEqual(settings["answer_model"], "qwen3:8b")

    def test_blank_values_use_defaults(self):
        values = ["", " ", "\t", "\n"]
        for value in values:
            with self.subTest(value=repr(value)), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                path = base / "config"
                path.mkdir()
                (path / "model_settings.json").write_text(json.dumps({
                    "window_verifier_model": value,
                    "answer_model": value,
                }), encoding="utf-8")
                settings = load_model_settings(base)
                self.assertEqual(settings["window_verifier_model"], "qwen3:4b")
                self.assertEqual(settings["answer_model"], "qwen3:8b")

    def test_wrong_types_use_defaults(self):
        bad_values = [None, 1, 0.5, [], {}, True]
        for bad in bad_values:
            with self.subTest(bad=repr(bad)), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                path = base / "config"
                path.mkdir()
                (path / "model_settings.json").write_text(json.dumps({
                    "window_verifier_model": bad,
                    "answer_model": bad,
                }), encoding="utf-8")
                settings = load_model_settings(base)
                self.assertEqual(settings["window_verifier_model"], "qwen3:4b")
                self.assertEqual(settings["answer_model"], "qwen3:8b")

    def test_custom_models_are_trimmed_and_loaded(self):
        cases = [
            ("llama3.2:3b", "qwen3:8b"),
            (" qwen3:4b ", " qwen3:8b "),
            ("custom/model:tag", "custom-answer:v1"),
            ("phi4", "qwen3:8b"),
        ]
        for window, answer in cases:
            with self.subTest(window=window, answer=answer), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                path = base / "config"
                path.mkdir()
                (path / "model_settings.json").write_text(json.dumps({
                    "window_verifier_model": window,
                    "answer_model": answer,
                }), encoding="utf-8")
                settings = load_model_settings(base)
                self.assertEqual(settings["window_verifier_model"], window.strip())
                self.assertEqual(settings["answer_model"], answer.strip())

    def test_unknown_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            path = base / "config"
            path.mkdir()
            (path / "model_settings.json").write_text(json.dumps({
                "window_verifier_model": "small",
                "answer_model": "large",
                "dangerous_extra": "ignored",
            }), encoding="utf-8")
            settings = load_model_settings(base)
        self.assertEqual(set(settings), {"window_verifier_model", "answer_model", "vision_model"})

    def test_one_missing_key_only_uses_one_default(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            path = base / "config"
            path.mkdir()
            (path / "model_settings.json").write_text(json.dumps({
                "answer_model": "qwen3:8b",
            }), encoding="utf-8")
            settings = load_model_settings(base)
        self.assertEqual(settings["window_verifier_model"], "qwen3:4b")
        self.assertEqual(settings["answer_model"], "qwen3:8b")


if __name__ == "__main__":
    unittest.main()
