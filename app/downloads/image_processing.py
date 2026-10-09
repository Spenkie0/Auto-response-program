from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from .models import DownloadIngestionError


@dataclass(frozen=True)
class ImageProcessingResult:
    """Describe validated image evidence and an optional sanitized image artifact."""

    status: str
    metadata: dict[str, Any]
    ocr: dict[str, Any]
    visual_ready: bool
    safe_image_bytes: bytes | None = None


def _load_pillow():
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    try:
        from PIL import Image, UnidentifiedImageError
        return Image, UnidentifiedImageError
    except Exception as exc:
        raise DownloadIngestionError(
            "IMAGE_DECODER_UNAVAILABLE",
            "Pillow is not available in the quarantine worker image.",
            details=str(exc),
            stage="IMAGE_PROCESSING",
            operation="decode_image",
            status="UNSUPPORTED",
        ) from exc


def _write_normalized_png(image, maximum_bytes: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if len(data) > maximum_bytes:
        raise DownloadIngestionError(
            "IMAGE_SANITIZED_OUTPUT_TOO_LARGE",
            "The sanitized image exceeds the configured safe-artifact output limit.",
            stage="IMAGE_PROCESSING",
            operation="normalize_image",
        )
    return data


def _run_tesseract(normalized_png: bytes, rules: dict[str, object]) -> dict[str, Any]:
    """Run Tesseract only against the re-encoded normalized PNG, never the original bytes."""
    if not bool(rules.get("ocr_enabled", True)):
        return {"status": "disabled", "engine": "tesseract", "text": None, "language": None}
    executable = str(rules.get("tesseract_executable", "tesseract"))
    language = str(rules.get("ocr_language", "fra+eng"))
    timeout = int(rules.get("ocr_timeout_seconds", 30))
    with tempfile.TemporaryDirectory(prefix="scrapper-ocr-") as temp_dir:
        source = Path(temp_dir) / "normalized.png"
        source.write_bytes(normalized_png)
        command = [executable, str(source), "stdout", "--psm", str(rules.get("ocr_psm", 6)), "-l", language]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return {"status": "unavailable", "engine": "tesseract", "text": None, "language": language, "error": "OCR_EXECUTABLE_NOT_FOUND"}
        except subprocess.TimeoutExpired as exc:
            return {"status": "timeout", "engine": "tesseract", "text": None, "language": language, "error": "OCR_TIMEOUT", "details": str(exc)[:1000]}
        except OSError as exc:
            return {"status": "error", "engine": "tesseract", "text": None, "language": language, "error": "OCR_EXECUTION_FAILED", "details": str(exc)[:1000]}
        text = (completed.stdout or "")[: int(rules.get("max_ocr_text_chars", rules.get("max_text_chars", 400000)))]
        if completed.returncode != 0:
            return {
                "status": "error",
                "engine": "tesseract",
                "text": text or None,
                "language": language,
                "error": "OCR_EXECUTION_FAILED",
                "return_code": completed.returncode,
                "stderr": (completed.stderr or "")[-2000:],
            }
        return {
            "status": "present" if text.strip() else "empty",
            "engine": "tesseract",
            "text": text,
            "language": language,
            "character_count": len(text),
        }


def process_image_bytes(raw: bytes, rules: dict[str, object], *, source_name: str = "image") -> ImageProcessingResult:
    """Decode and normalize one validated image inside the quarantine boundary."""
    Image, UnidentifiedImageError = _load_pillow()
    max_pixels = int(rules.get("max_image_pixels", 40_000_000))
    max_dimension = int(rules.get("max_image_dimension", 20_000))
    max_safe_bytes = int(rules.get("max_safe_image_output_kb", 512)) * 1024

    try:
        with Image.open(io.BytesIO(raw)) as image:
            source_format = str(image.format or "").upper() or None
            width, height = image.size
            if width <= 0 or height <= 0 or width > max_dimension or height > max_dimension:
                raise DownloadIngestionError(
                    "IMAGE_DIMENSIONS_EXCEEDED",
                    f"Image dimensions {width}x{height} exceed the configured safety limits.",
                    stage="IMAGE_PROCESSING",
                    operation="validate_dimensions",
                )
            if width * height > max_pixels:
                raise DownloadIngestionError(
                    "IMAGE_PIXEL_COUNT_EXCEEDED",
                    f"Image contains {width * height:,} pixels; maximum is {max_pixels:,}.",
                    stage="IMAGE_PROCESSING",
                    operation="validate_dimensions",
                )
            try:
                image.verify()
            except Exception as exc:
                raise DownloadIngestionError(
                    "IMAGE_CORRUPTED",
                    f"Image decoder rejected the image data: {exc}",
                    stage="IMAGE_PROCESSING",
                    operation="verify_image",
                ) from exc

        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            width, height = image.size
            metadata = {
                "name": source_name,
                "format": source_format,
                "dimensions": {"width": width, "height": height},
                "pixel_count": width * height,
                "mode": image.mode,
                "animated": bool(getattr(image, "n_frames", 1) > 1),
                "frames": int(getattr(image, "n_frames", 1)),
                "metadata_keys": sorted(str(key) for key in getattr(image, "info", {}).keys()),
            }
            exif = None
            try:
                exif_data = image.getexif()
                if exif_data:
                    exif = {str(tag): str(value)[:500] for tag, value in exif_data.items() if tag in {270, 271, 272, 274, 306, 36867, 36868}}
            except Exception:
                exif = None
            metadata["exif"] = exif
            # Normalize the decoded pixels before OCR or any host-side optional vision stage.
            normalized = image.convert("RGB")
            safe_bytes = _write_normalized_png(normalized, max_safe_bytes)

    except UnidentifiedImageError as exc:
        raise DownloadIngestionError(
            "IMAGE_INVALID",
            f"The image decoder could not identify {source_name!r}.",
            details=str(exc),
            stage="IMAGE_PROCESSING",
            operation="decode_image",
        ) from exc

    ocr = _run_tesseract(safe_bytes, rules)
    return ImageProcessingResult(
        status="accepted",
        metadata=metadata,
        ocr=ocr,
        visual_ready=True,
        safe_image_bytes=safe_bytes,
    )


def process_image_file(path: Path, rules: dict[str, object]) -> ImageProcessingResult:
    """Read one image within the trusted quarantine worker and process it safely."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DownloadIngestionError("IMAGE_READ_FAILED", f"Could not read image: {exc}", stage="IMAGE_PROCESSING", operation="read_image") from exc
    return process_image_bytes(raw, rules, source_name=path.name)
