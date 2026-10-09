from __future__ import annotations

import io
import hashlib
from pathlib import Path
import zipfile
from urllib.parse import urlsplit

from defusedxml import ElementTree as SafeET

from .image_processing import process_image_bytes
from .models import DownloadContent, DownloadIngestionError, ValidationResult
from .evidence import build_evidence


def _decode_text(raw: bytes) -> str:
    """Decode a bounded text payload with explicit UTF-8/UTF-16 handling."""
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DownloadIngestionError("TEXT_DECODE_FAILED", "The accepted text file could not be safely decoded.", stage="FORMAT_PROCESSING", operation="decode_text")


def _docx_text(xml_bytes: bytes) -> str:
    """Extract readable paragraph text from a DOCX document.xml payload."""
    try:
        root = SafeET.fromstring(xml_bytes)
    except Exception as exc:
        raise DownloadIngestionError("DOCUMENT_DOCX_XML_INVALID", "DOCX document.xml is not safely parseable.", details=str(exc), stage="DOCUMENT_TEXT", operation="parse_docx_xml") from exc
    pieces: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "t" and element.text:
            pieces.append(element.text)
        elif tag == "tab":
            pieces.append("\t")
        elif tag == "br":
            pieces.append("\n")
        elif tag == "p":
            pieces.append("\n")
    return "".join(pieces).strip()


def _odt_text(xml_bytes: bytes) -> str:
    """Extract readable paragraph text from an ODT content.xml payload."""
    try:
        root = SafeET.fromstring(xml_bytes)
    except Exception as exc:
        raise DownloadIngestionError("DOCUMENT_ODT_XML_INVALID", "ODT content.xml is not safely parseable.", details=str(exc), stage="DOCUMENT_TEXT", operation="parse_odt_xml") from exc
    pieces: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in {"p", "h"}:
            text = "".join(element.itertext()).strip()
            if text:
                pieces.append(text)
        elif tag == "s":
            pieces.append(" ")
        elif tag == "line-break":
            pieces.append("\n")
    return "\n".join(pieces).strip()


def _read_zip_member(path: Path, member: str, *, max_bytes: int | None = None) -> bytes:
    """Read one known validated package member without extracting it to disk."""
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(member)
            if max_bytes is not None and info.file_size > max_bytes:
                raise DownloadIngestionError("DOCUMENT_MEMBER_TOO_LARGE", f"Document member {member!r} exceeds the configured processing limit.", stage="DOCUMENT_TEXT", operation="read_document_member", status="REJECTED")
            return archive.read(info)
    except KeyError as exc:
        raise DownloadIngestionError("DOCUMENT_REQUIRED_MEMBER_MISSING", f"Required document member is missing: {member}", stage="DOCUMENT_TEXT", operation="read_document_member") from exc
    except Exception as exc:
        raise DownloadIngestionError("DOCUMENT_ARCHIVE_READ_FAILED", f"Could not read archive member {member!r}.", details=str(exc), stage="DOCUMENT_TEXT", operation="read_document_member") from exc


def _validate_relationships(path: Path, validation: ValidationResult, rules: dict[str, object]) -> dict[str, object]:
    """Inspect package relationships without ever fetching external targets."""
    result = {"status": "non-existent", "count": 0, "external": []}
    if validation.detection.archive_kind not in {"docx", "odt"}:
        return result
    relationship_names = [entry.name for entry in validation.detection.archive_entries if entry.name.lower().endswith(".rels")]
    if not relationship_names:
        result["status"] = "present"
        return result
    maximum = int(rules.get("max_relationship_count", 10000))
    external: list[str] = []
    count = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for name in relationship_names:
                if "!/" in name:
                    continue
                try:
                    raw = archive.read(name)
                except KeyError:
                    continue
                if len(raw) > int(rules.get("max_xml_bytes", 10_000_000)):
                    raise DownloadIngestionError("DOCUMENT_RELATIONSHIP_XML_TOO_LARGE", f"Relationship XML exceeds the configured limit: {name}", stage="DOCUMENT_TEXT", operation="validate_relationships", status="REJECTED")
                try:
                    root = SafeET.fromstring(raw)
                except Exception as exc:
                    raise DownloadIngestionError("DOCUMENT_RELATIONSHIP_XML_INVALID", f"Relationship XML is malformed: {name}", details=str(exc), stage="DOCUMENT_TEXT", operation="validate_relationships", status="REJECTED") from exc
                for element in root.iter():
                    if element.tag.rsplit("}", 1)[-1].lower() != "relationship":
                        continue
                    count += 1
                    if count > maximum:
                        raise DownloadIngestionError("DOCUMENT_RELATIONSHIP_COUNT_EXCEEDED", "Document contains more relationships than the configured limit.", stage="DOCUMENT_TEXT", operation="validate_relationships", status="REJECTED")
                    target = str(element.attrib.get("Target") or "")
                    mode = str(element.attrib.get("TargetMode") or "").lower()
                    parsed = urlsplit(target)
                    if mode == "external" or parsed.scheme in {"http", "https", "file", "ftp", "smb", "data", "gopher"} or target.startswith("//"):
                        external.append(target[:1000])
    except zipfile.BadZipFile as exc:
        raise DownloadIngestionError("DOCUMENT_ARCHIVE_READ_FAILED", "Could not validate package relationships.", details=str(exc), stage="DOCUMENT_TEXT", operation="validate_relationships") from exc
    result.update({"status": "present", "count": count, "external": external})
    if external and not bool(rules.get("allow_external_relationships", False)):
        raise DownloadIngestionError("DOCUMENT_EXTERNAL_RESOURCE_BLOCKED", "The document contains external relationships/resources and the configured policy forbids them.", details={"count": len(external)}, stage="SECURITY_VALIDATION", operation="validate_external_relationships", status="REJECTED")
    return result


def _embedded_image_names(validation: ValidationResult) -> list[str]:
    """Return image-bearing member paths discovered by recursive container inspection."""
    names: list[str] = []
    for entry in validation.detection.archive_entries:
        if entry.is_directory:
            continue
        suffix = Path(entry.name).suffix.lower()
        lower = entry.name.lower().replace("\\", "/")
        if suffix in {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico", ".heif", ".heic", ".avif"}:
            names.append(entry.name)
        elif lower.startswith(("word/media/", "ppt/media/", "xl/media/", "pictures/")):
            names.append(entry.name)
    return list(dict.fromkeys(names))


def _process_embedded_images(path: Path, validation: ValidationResult, rules: dict[str, object]) -> tuple[list[dict[str, object]], list[bytes]]:
    """Process each embedded image in the same Docker worker boundary."""
    items: list[dict[str, object]] = []
    safe_artifacts: list[bytes] = []
    max_embedded = int(rules.get("max_embedded_images", 32))
    max_total_safe = int(rules.get("max_safe_image_output_kb", 512)) * 1024
    total_safe = 0
    for name in _embedded_image_names(validation)[:max_embedded]:
        try:
            with zipfile.ZipFile(path) as archive:
                raw = archive.read(name)
            image_result = process_image_bytes(raw, rules, source_name=name)
            safe = image_result.safe_image_bytes
            if safe and total_safe + len(safe) <= max_total_safe:
                safe_artifacts.append(safe)
                total_safe += len(safe)
            items.append({
                "name": name,
                "status": image_result.status,
                "metadata": image_result.metadata,
                "ocr": image_result.ocr,
                "visual_ready": image_result.visual_ready,
                "safe_artifact_sha256": hashlib.sha256(safe).hexdigest() if safe else None,
                "source": "archive_member",
            })
        except DownloadIngestionError as exc:
            items.append({
                "name": name,
                "status": "error",
                "error_code": exc.code,
                "error_message": str(exc),
            })
    return items, safe_artifacts


def extract_safe_text(path: Path, validation: ValidationResult, max_chars: int, rules: dict[str, object] | None = None) -> DownloadContent:
    """Process validated content deterministically inside the quarantine worker."""
    if not validation.accepted:
        raise DownloadIngestionError("SECURITY_FILE_REJECTED", validation.reason, details=validation, stage="SECURITY_VALIDATION", operation="accept_file", status="REJECTED")

    effective_rules = rules or {}
    extension = validation.effective_extension
    detection = validation.detection
    image_kinds = {"jpeg", "png", "gif", "webp", "bmp", "tiff", "ico", "heif", "avif", "svg"}
    text = ""
    safe_artifacts: list[bytes] = []
    image_analysis: list[dict[str, object]] = []
    relationships: dict[str, object] = {"status": "non-existent", "count": 0, "external": []}

    if detection.kind in {"text", "csv"}:
        text = _decode_text(path.read_bytes())
    elif detection.archive_kind == "docx":
        relationships = _validate_relationships(path, validation, effective_rules)
        text = _docx_text(_read_zip_member(path, "word/document.xml", max_bytes=int(effective_rules.get("max_xml_bytes", 10_000_000))))
        image_analysis, safe_artifacts = _process_embedded_images(path, validation, effective_rules)
    elif detection.archive_kind == "odt":
        relationships = _validate_relationships(path, validation, effective_rules)
        text = _odt_text(_read_zip_member(path, "content.xml", max_bytes=int(effective_rules.get("max_xml_bytes", 10_000_000))))
        image_analysis, safe_artifacts = _process_embedded_images(path, validation, effective_rules)
    elif detection.kind in image_kinds:
        if detection.kind == "svg":
            text = ""
        else:
            image_result = process_image_bytes(path.read_bytes(), effective_rules, source_name=path.name)
            image_analysis.append({
                "name": path.name,
                "status": image_result.status,
                "metadata": image_result.metadata,
                "ocr": image_result.ocr,
                "visual_ready": image_result.visual_ready,
                "safe_artifact_sha256": hashlib.sha256(image_result.safe_image_bytes).hexdigest() if image_result.safe_image_bytes else None,
                "source": "downloaded_file",
            })
            if image_result.safe_image_bytes:
                safe_artifacts.append(image_result.safe_image_bytes)
    else:
        raise DownloadIngestionError(
            "FORMAT_UNSUPPORTED",
            f"No safe parser is configured for detected type {detection.kind!r} (accepted extension policy allowed {extension!r}).",
            stage="FORMAT_PROCESSING",
            operation="select_parser",
            status="UNSUPPORTED",
        )

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]

    if not text.strip() and detection.kind not in image_kinds:
        raise DownloadIngestionError("DOCUMENT_EMPTY_TEXT", "The accepted document contains no extractable text.", stage="DOCUMENT_TEXT", operation="validate_extracted_text")

    archive_summary = [
        {
            "name": entry.name,
            "compressed_size": entry.compressed_size,
            "uncompressed_size": entry.uncompressed_size,
            "sha256": entry.sha256,
            "detected_kind": entry.detected_kind,
            "canonical_extension": entry.canonical_extension,
            "security_findings": list(entry.security_findings),
            "depth": entry.depth,
            "processing_status": entry.processing_status,
        }
        for entry in detection.archive_entries
    ]

    metadata = {
        "source_name": path.name,
        "detected_kind": detection.kind,
        "detected_extension": detection.canonical_extension,
        "effective_extension": extension,
        "archive_kind": detection.archive_kind,
        "archive_entries": archive_summary,
        "archive_member_previews": detection.archive_member_previews,
        "security_findings": list(validation.security_findings),
        "security_status": validation.security_status,
        "url_extension": validation.url_extension,
        "url_extension_match": validation.url_match,
        "declared_content_type": validation.declared_mime_type,
        "mime_match": validation.mime_match,
        "content_disposition": validation.content_disposition,
        "filename": validation.filename,
        "validation_strength": validation.validation_strength,
        "file_sha256": validation.file_sha256,
        "truncated": truncated,
        "character_count": len(text),
        "image_analysis": image_analysis,
        "relationships": relationships,
        "safe_image_artifact_count": len(safe_artifacts),
    }
    evidence = build_evidence(
        path,
        validation,
        extracted_text=None if detection.kind in image_kinds else text,
        sha256=validation.file_sha256,
        metadata_overrides=metadata,
    )
    if image_analysis:
        image_section = evidence["images"]
        if isinstance(image_section, dict):
            image_section.update({"status": "present", "items": image_analysis, "count": len(image_analysis)})
        ocr_items = [item.get("ocr") for item in image_analysis if item.get("ocr")]
        ocr_section = evidence["ocr"]
        if isinstance(ocr_section, dict) and ocr_items:
            ocr_section.update({"status": "present", "items": ocr_items, "text": "\n".join(str(item.get("text") or "") for item in ocr_items if isinstance(item, dict))})

    return DownloadContent(
        text=text,
        validation=validation,
        metadata=metadata,
        evidence=evidence,
        safe_image_artifacts=tuple(safe_artifacts),
    )
