from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .config import SCREENSHOT_EVIDENCE_FOLDER, data_dir


SCREENSHOT_EVIDENCE_SCHEMA_VERSION = "1.0"


def empty_screenshot_evidence() -> dict[str, object]:
    """Return the stable screenshot/OCR/visual-analysis evidence schema."""
    return {
        "schema_version": SCREENSHOT_EVIDENCE_SCHEMA_VERSION,
        "screenshot": {
            "status": "non-existent",
            "path": None,
            "width": None,
            "height": None,
        },
        "ocr": {
            "status": "non-existent",
            "engine": None,
            "language": None,
            "text": None,
            "character_count": None,
        },
        "visual_analysis": {
            "status": "non-existent",
            "model": None,
            "summary": None,
            "observations": None,
            "confidence": None,
        },
    }


def _screenshot_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Read PNG dimensions without decoding it in Python; return nulls on failure."""
    try:
        payload = path.read_bytes()
        if payload[:8] != b"\x89PNG\r\n\x1a\n" or len(payload) < 24:
            return None, None
        width = int.from_bytes(payload[16:20], "big")
        height = int.from_bytes(payload[20:24], "big")
        return width, height
    except OSError:
        return None, None


def _ocr_languages_available(executable: str, tessdata_base_dir: Path | None = None) -> set[str] | None:
    """Return installed Tesseract language codes, optionally using the managed data root."""
    command = [executable, "--list-langs"]
    if tessdata_base_dir is not None:
        command.extend(["--tessdata-dir", str(tessdata_base_dir)])
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    languages: set[str] = set()
    for line in result.stdout.splitlines():
        value = line.strip()
        if not value or value.lower().startswith("list of available languages in "):
            continue
        value = value.replace("\\", "/")
        if value.lower().startswith("tessdata/"):
            value = value.split("/", 1)[1]
        if value:
            languages.add(value)
    return languages


def run_ocr(
    screenshot_path: Path,
    *,
    executable: str = "tesseract",
    language: str = "fra+eng",
    tessdata_base_dir: Path | None = None,
) -> dict[str, object]:
    """Run Tesseract OCR on a trusted screenshot path and return structured evidence."""
    result: dict[str, object] = {
        "status": "non-existent",
        "engine": None,
        "language": None,
        "text": None,
        "character_count": None,
    }
    if not screenshot_path.exists():
        return result

    available = _ocr_languages_available(executable, tessdata_base_dir)
    if available is None:
        result.update({"status": "unavailable", "engine": "tesseract", "language": language})
        return result

    requested = [part.strip() for part in language.split("+") if part.strip()]
    usable = [part for part in requested if part in available]
    if not usable:
        usable = ["eng"] if "eng" in available else []
    if not usable:
        result.update({"status": "unavailable", "engine": "tesseract", "language": language})
        return result

    selected_language = "+".join(usable)
    try:
        command = [executable]
        if tessdata_base_dir is not None:
            command.extend(["--tessdata-dir", str(tessdata_base_dir)])
        command.extend([str(screenshot_path), "stdout", "-l", selected_language, "--psm", "6"])
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        result.update({"status": "unavailable", "engine": "tesseract", "language": selected_language})
        return result

    if process.returncode != 0:
        result.update({"status": "error", "engine": "tesseract", "language": selected_language})
        return result

    text = process.stdout.strip()
    result.update(
        {
            "status": "present",
            "engine": "tesseract",
            "language": selected_language,
            "text": text or None,
            "character_count": len(text),
        }
    )
    return result


def run_visual_analysis(
    screenshot_path: Path,
    *,
    ollama: Any,
    model: str,
) -> dict[str, object]:
    """Ask a configured vision-capable Ollama model for structured visual observations."""
    result: dict[str, object] = {
        "status": "non-existent",
        "model": None,
        "summary": None,
        "observations": None,
        "confidence": None,
    }
    if not screenshot_path.exists():
        return result
    if not model:
        result.update({"status": "unavailable", "model": None})
        return result

    prompt = (
        "Analyze the supplied webpage screenshot as visual evidence. "
        "Do not solve the webpage question. Describe visible layout, labels, controls, "
        "dialogs, tables, charts, relevant objects, and anything visually important "
        "that may not be represented faithfully in HTML. Return JSON only with keys: "
        "summary (string), observations (array of strings), confidence (number 0..1)."
    )
    try:
        output = ollama.chat_json(
            model,
            prompt,
            "Return only the requested visual evidence JSON.",
            images=[screenshot_path],
        )
    except Exception:
        result.update({"status": "error", "model": model})
        return result

    summary = output.get("summary") if isinstance(output, dict) else None
    observations = output.get("observations") if isinstance(output, dict) else None
    confidence = output.get("confidence") if isinstance(output, dict) else None
    if not isinstance(summary, str):
        summary = None
    if not isinstance(observations, list):
        observations = None
    try:
        confidence_value = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_value = None
    if confidence_value is not None and not 0.0 <= confidence_value <= 1.0:
        confidence_value = None

    result.update(
        {
            "status": "present" if summary or observations else "error",
            "model": model,
            "summary": summary,
            "observations": observations,
            "confidence": confidence_value,
        }
    )
    return result


def analyze_screenshot(
    base_dir: Path,
    screenshot_path: Path | None,
    *,
    ocr_enabled: bool = True,
    ocr_executable: str = "tesseract",
    ocr_language: str = "fra+eng",
    ocr_tessdata_base_dir: Path | None = None,
    visual_analysis_enabled: bool = True,
    ollama: Any = None,
    vision_model: str = "",
) -> dict[str, object]:
    """Build screenshot evidence, optionally adding OCR and vision-model observations."""
    evidence = empty_screenshot_evidence()
    if screenshot_path is None or not screenshot_path.exists():
        return evidence

    width, height = _screenshot_dimensions(screenshot_path)
    screenshot = evidence["screenshot"]
    assert isinstance(screenshot, dict)
    screenshot.update(
        {
            "status": "present",
            "path": screenshot_path.name,
            "width": width,
            "height": height,
        }
    )

    if ocr_enabled:
        evidence["ocr"] = run_ocr(
            screenshot_path,
            executable=ocr_executable,
            language=ocr_language,
            tessdata_base_dir=ocr_tessdata_base_dir,
        )
    else:
        evidence["ocr"] = {
            "status": "disabled",
            "engine": "tesseract",
            "language": ocr_language,
            "text": None,
            "character_count": None,
        }

    if visual_analysis_enabled and ollama is not None and vision_model:
        evidence["visual_analysis"] = run_visual_analysis(
            screenshot_path,
            ollama=ollama,
            model=vision_model,
        )
    elif visual_analysis_enabled:
        evidence["visual_analysis"] = {
            "status": "unavailable",
            "model": None,
            "summary": None,
            "observations": None,
            "confidence": None,
        }
    else:
        evidence["visual_analysis"] = {
            "status": "disabled",
            "model": vision_model or None,
            "summary": None,
            "observations": None,
            "confidence": None,
        }

    evidence_dir = data_dir(base_dir) / SCREENSHOT_EVIDENCE_FOLDER
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = evidence_dir / f"{screenshot_path.stem}.json"
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence["evidence_path"] = str(target)
    return evidence


def print_screenshot_cli(evidence: dict[str, object]) -> None:
    """Print a concise but detailed screenshot/OCR/visual-analysis summary."""
    screenshot = evidence.get("screenshot", {})
    ocr = evidence.get("ocr", {})
    visual = evidence.get("visual_analysis", {})
    print("[SCREENSHOT]")
    print(f"  file       : {screenshot.get('path') or '(none)'}")
    if screenshot.get("width") and screenshot.get("height"):
        print(f"  dimensions : {screenshot['width']}x{screenshot['height']}")
    print(f"  OCR        : {ocr.get('status')} ({ocr.get('engine') or 'n/a'}, {ocr.get('language') or 'n/a'})")
    print(f"  OCR chars  : {ocr.get('character_count') if ocr.get('character_count') is not None else 'n/a'}")
    if ocr.get("text"):
        preview = str(ocr["text"]).replace("\n", " ")[:280]
        print(f"  OCR preview: {preview}{'...' if len(str(ocr['text'])) > 280 else ''}")
    print(f"  visual     : {visual.get('status')} ({visual.get('model') or 'n/a'})")
    if visual.get("summary"):
        print(f"  visual     : {str(visual['summary'])[:320]}")
    if evidence.get("evidence_path"):
        print(f"  evidence   : {evidence['evidence_path']}")
