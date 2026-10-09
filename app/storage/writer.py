import json
import base64
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import ANSWER_FOLDER, ANSWER_NOT_FOUND_FOLDER, CLEAN_FOLDER, RAW_FOLDER, REVIEW_FOLDER, SCREENSHOT_FOLDER, data_dir


def safe_name(value: str) -> str:
    """Sanitize a string for conservative Windows-compatible filenames."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "page")
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    return value[:180] or "page"


def unique_path(path: Path) -> Path:
    """Return an unused path, adding a numeric suffix when needed."""
    if not path.exists():
        return path
    for i in range(2, 10000):
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a unique output filename for {path.name}.")


def ensure_dir(base_dir: Path, name: str) -> Path:
    """Create and return one named subdirectory under data."""
    folder = data_dir(base_dir) / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_text(path: Path, content: str, overwrite: bool = False) -> Path:
    """Write UTF-8 text to a file while optionally avoiding collisions."""
    target = path if overwrite else unique_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target



def write_bytes(path: Path, content: bytes, overwrite: bool = False) -> Path:
    """Write raw bytes to a file while optionally avoiding collisions."""
    target = path if overwrite else unique_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target

def timestamp_filename() -> str:
    """Return a Windows-safe, collision-resistant UTC timestamp filename stem."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")



def save_screenshot(base_dir: Path, stem: str, data_url: str, overwrite: bool = False) -> Path:
    """Decode a Firefox PNG data URL and save it beside the capture data."""
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ValueError("Firefox screenshot is not a PNG data URL.")
    encoded = data_url[len(prefix):]
    try:
        payload = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Firefox screenshot base64 data is invalid.") from exc
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Firefox screenshot does not contain a valid PNG signature.")
    folder = ensure_dir(base_dir, SCREENSHOT_FOLDER)
    return write_bytes(folder / f"{safe_name(stem)}.png", payload, overwrite=overwrite)

def save_raw_page(base_dir: Path, title: str, html: str, overwrite: bool = False) -> Path:
    """Save a raw capture using a timestamp, never the page title.

    The title remains available to the caller for logs/metadata, but it is not
    used as a filesystem identifier. This avoids Unicode, reserved-character,
    reserved-name, and path-length issues caused by arbitrary website titles.
    """
    folder = ensure_dir(base_dir, RAW_FOLDER)
    filename = f"{timestamp_filename()}.html"
    return write_text(folder / filename, html, overwrite=overwrite)


def save_clean_page(base_dir: Path, raw_path: Path, html: str, overwrite: bool = False) -> Path:
    """Save cleaned HTML using the raw capture timestamp."""
    folder = ensure_dir(base_dir, CLEAN_FOLDER)
    return write_text(folder / raw_path.name, html, overwrite=overwrite)



def save_review(base_dir: Path, stem: str, payload: dict[str, object], overwrite: bool = False) -> Path:
    """Save an ambiguous extraction review record as JSON."""
    folder = ensure_dir(base_dir, REVIEW_FOLDER)
    return write_text(
        folder / f"{safe_name(stem)}.json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        overwrite=overwrite,
    )


def save_answer_json(
    base_dir: Path,
    clean_path: Path,
    decision: dict,
    overwrite: bool = False,
) -> Path:
    """Save exactly one JSON decision file in the appropriate answer folder.

    The cleaned HTML remains in data/clean_window_pages/ as the canonical
    processed page. Answer folders contain only the model's JSON decision.
    """
    destination = ANSWER_FOLDER if decision.get("status") == "answer" else ANSWER_NOT_FOUND_FOLDER
    folder = ensure_dir(base_dir, destination)
    target = folder / clean_path.with_suffix(".json").name
    payload = dict(decision)
    payload.setdefault("source_clean_page", str(clean_path))
    return write_text(
        target,
        json.dumps(payload, ensure_ascii=False, indent=2),
        overwrite=overwrite,
    )


def save_result(base_dir: Path, title: str, result: dict[str, object], output_format: str, overwrite: bool = False) -> Path:
    """Save a legacy result representation; current code uses dedicated writers."""
    folder = ensure_dir(base_dir, RAW_FOLDER)
    suffix = {"html": ".html", "json": ".json", "txt": ".txt"}[output_format]
    path = folder / f"{safe_name(title)}{suffix}"

    if output_format == "html":
        content = result["html"]
    elif output_format == "json":
        content = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        content = result["html"]

    return write_text(path, content, overwrite=overwrite)
