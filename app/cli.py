import argparse

from .config import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_VISION_MODEL,
    MODEL_SETTINGS_FILE,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_COMPACT_ANSWER_CHARS,
    DEFAULT_WINDOW_MODEL,
    load_vision_settings,
    VISION_SETTINGS_FILE,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the scraper pipeline."""
    p = argparse.ArgumentParser(
        description=(
            "Process captured Firefox pages through the HTML window extractor and Ollama. "
            "Capture is normally triggered from the Firefox extension."
        )
    )

    p.add_argument("--overwrite", action="store_true",
                   help="Overwrite output files when safe to do so")

    p.add_argument("--window-model", default=DEFAULT_WINDOW_MODEL,
                   help=f"Small Ollama model used only for ambiguous extraction (default: {DEFAULT_WINDOW_MODEL}; configured in {MODEL_SETTINGS_FILE})")
    p.add_argument("--answer-model", default=DEFAULT_ANSWER_MODEL,
                   help=f"Main Ollama model used to answer clean pages (default: {DEFAULT_ANSWER_MODEL}; configured in {MODEL_SETTINGS_FILE})")
    p.add_argument("--vision-model", default=DEFAULT_VISION_MODEL,
                   help=f"Optional vision-capable Ollama model used when the captured screenshot should be sent to AI (default: {DEFAULT_VISION_MODEL or 'disabled'}; configured in {MODEL_SETTINGS_FILE})")
    p.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST,
                   help=f"Ollama HTTP base URL (default: {DEFAULT_OLLAMA_HOST})")
    p.add_argument("--ollama-timeout", type=int, default=180,
                   help="Ollama request timeout in seconds")
    p.add_argument("--no-window-llm", action="store_true",
                   help="Do not use the small model as an extraction fallback")
    p.add_argument("--no-answer-llm", action="store_true",
                   help="Capture/extract only; do not send clean pages to the answer model")
    p.add_argument("--no-screenshot", action="store_true",
                   help="Do not use the captured screenshot even when a vision model is configured")
    vision_defaults = load_vision_settings()
    p.add_argument("--no-ocr", action="store_true",
                   help=f"Disable screenshot OCR (default: enabled; configured in {VISION_SETTINGS_FILE})")
    p.add_argument("--ocr-language", default=str(vision_defaults.get("ocr_language", "fra+eng")),
                   help=f"Tesseract OCR language(s), e.g. fra+eng (default: {vision_defaults.get('ocr_language', 'fra+eng')}; configured in {VISION_SETTINGS_FILE})")
    p.add_argument("--no-visual-analysis", action="store_true",
                   help=f"Disable optional vision-model screenshot analysis (default: enabled; configured in {VISION_SETTINGS_FILE})")
    p.add_argument("--max-answer-chars", type=int, default=120000,
                   help="Maximum clean HTML characters considered for the answer model")
    p.add_argument("--answer-input-mode", choices=["compact", "full"], default="compact",
                   help="What the answer model receives: compact semantic representation (default) or full clean HTML")
    p.add_argument("--compact-answer-chars", type=int, default=DEFAULT_COMPACT_ANSWER_CHARS,
                   help=f"Maximum size of the compact question representation (default: {DEFAULT_COMPACT_ANSWER_CHARS})")

    p.add_argument("--process-existing", action="store_true",
                   help="Process .html files already present in data/raw_pages")
    p.add_argument("--check-models", action="store_true",
                   help="Check the configured Ollama server and show whether the selected models are installed")
    p.add_argument("--setup-dependencies", action="store_true",
                   help="Check/install Python, Python libraries, Firefox, Docker Desktop, Tesseract, Ollama, models, and the Docker quarantine image")
    p.add_argument("--file", type=str,
                   help="With --process-existing, process only this raw HTML filename")
    p.add_argument("--url", type=str, default="",
                   help="Optional URL metadata for the selected raw page")
    p.add_argument("--title", type=str, default="",
                   help="Optional title metadata for the selected raw page")
    p.add_argument("--attachment", type=str,
                   help="Optional downloaded file to quarantine, safely inspect, extract as text, and provide to the answer model")
    p.add_argument("--inspect-download", type=str,
                   help="Inspect a downloaded file with Python byte/signature/archive analysis without sending it to Ollama")
    p.add_argument("--dry-run", action="store_true",
                   help="Run extraction and show decisions without moving files or calling Ollama")

    return p
