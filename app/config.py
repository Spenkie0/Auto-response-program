from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_FOLDER = "data"
RAW_FOLDER = "raw_pages"
CLEAN_FOLDER = "clean_window_pages"
ANSWER_FOLDER = "answer"
ANSWER_NOT_FOUND_FOLDER = "answer_not_found"
REVIEW_FOLDER = "review"
ERROR_FOLDER = "errors"
DEBUG_OLLAMA_FOLDER = "debug/ollama"
DOWNLOAD_QUARANTINE_FOLDER = "downloads/quarantine"
DOWNLOAD_PROCESSED_FOLDER = "downloads/processed"
DOWNLOAD_REJECTED_FOLDER = "downloads/rejected"
DOWNLOAD_EVIDENCE_FOLDER = "downloads/evidence"
SCREENSHOT_FOLDER = "screenshots"
SCREENSHOT_EVIDENCE_FOLDER = "screenshots/evidence"

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_TIMEOUT = 180
DEFAULT_WINDOW_MIN_SCORE = 55.0
DEFAULT_WINDOW_MIN_MARGIN = 12.0
DEFAULT_MAX_CANDIDATES = 8
DEFAULT_MAX_CANDIDATE_CHARS = 5000
DEFAULT_MAX_ANSWER_CHARS = 120000
DEFAULT_COMPACT_ANSWER_CHARS = 40000

MODEL_SETTINGS_FILE = "config/model_settings.json"
_FALLBACK_WINDOW_MODEL = "qwen3:4b"
_FALLBACK_ANSWER_MODEL = "qwen3:8b"


def default_base_dir() -> Path:
    """Return the project root used for configuration, code, and runtime data."""
    return Path(__file__).resolve().parent.parent


def data_dir(base_dir: Path | None = None) -> Path:
    """Return and create the project runtime data directory."""
    base = Path(base_dir) if base_dir is not None else default_base_dir()
    path = base / DATA_FOLDER
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_path(*parts: str) -> Path:
    """Return a path inside the project root from path components."""
    return default_base_dir().joinpath(*parts)


def load_model_settings(base_dir: Path | None = None) -> dict[str, str]:
    """Load the configured Ollama model names, falling back to safe defaults if unavailable."""
    base = Path(base_dir) if base_dir is not None else default_base_dir()
    path = base / MODEL_SETTINGS_FILE
    defaults = {
        "window_verifier_model": _FALLBACK_WINDOW_MODEL,
        "answer_model": _FALLBACK_ANSWER_MODEL,
        "vision_model": "",
    }
    if not path.exists():
        return defaults

    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults

    if not isinstance(raw, dict):
        return defaults

    settings = dict(defaults)
    for key in settings:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            settings[key] = value.strip()
    return settings



VISION_SETTINGS_FILE = "config/vision_settings.json"
_DEFAULT_VISION_SETTINGS = {
    "ocr_enabled": True,
    "ocr_executable": "tesseract",
    "ocr_language": "fra+eng",
    "visual_analysis_enabled": True,
}


def load_vision_settings(base_dir: Path | None = None) -> dict[str, object]:
    """Load screenshot OCR/visual-analysis settings from the project config."""
    base = Path(base_dir) if base_dir is not None else default_base_dir()
    path = base / VISION_SETTINGS_FILE
    if not path.exists():
        return dict(_DEFAULT_VISION_SETTINGS)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT_VISION_SETTINGS)
    if not isinstance(raw, dict):
        return dict(_DEFAULT_VISION_SETTINGS)
    settings = dict(_DEFAULT_VISION_SETTINGS)
    if isinstance(raw.get("ocr_enabled"), bool):
        settings["ocr_enabled"] = raw["ocr_enabled"]
    if isinstance(raw.get("ocr_executable"), str) and raw["ocr_executable"].strip():
        settings["ocr_executable"] = raw["ocr_executable"].strip()
    if isinstance(raw.get("ocr_language"), str) and raw["ocr_language"].strip():
        settings["ocr_language"] = raw["ocr_language"].strip()
    if isinstance(raw.get("visual_analysis_enabled"), bool):
        settings["visual_analysis_enabled"] = raw["visual_analysis_enabled"]
    return settings

_MODEL_SETTINGS = load_model_settings()
DEFAULT_WINDOW_MODEL = _MODEL_SETTINGS["window_verifier_model"]
DEFAULT_ANSWER_MODEL = _MODEL_SETTINGS["answer_model"]
DEFAULT_VISION_MODEL = _MODEL_SETTINGS.get("vision_model", "")
