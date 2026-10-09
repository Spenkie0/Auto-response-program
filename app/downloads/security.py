from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit

from .detector import detect_file_type
from .models import DetectionResult, DownloadIngestionError, ValidationResult

_GENERIC_MIME_TYPES = {
    "application/octet-stream",
    "binary/octet-stream",
    "application/download",
    "application/force-download",
}

_MIME_CANONICAL: dict[str, set[str]] = {
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".odt": {"application/vnd.oasis.opendocument.text"},
    ".txt": {"text/plain"},
    ".csv": {"text/csv", "application/csv", "text/plain"},
    ".jpg": {"image/jpeg"},
    ".png": {"image/png"},
    ".gif": {"image/gif"},
    ".webp": {"image/webp"},
    ".bmp": {"image/bmp", "image/x-ms-bmp"},
    ".tif": {"image/tiff"},
    ".ico": {"image/x-icon", "image/vnd.microsoft.icon"},
    ".heif": {"image/heif", "image/heic"},
    ".avif": {"image/avif"},
    ".svg": {"image/svg+xml"},
    ".zip": {"application/zip", "application/x-zip-compressed"},
}

_IMAGE_KINDS = {"jpeg", "png", "gif", "webp", "bmp", "tiff", "ico", "heif", "avif", "svg"}


def extract_url_extension(url: str | None) -> str | None:
    """Extract the URL path extension only; query strings/fragments never invent one."""
    if not url:
        return None
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    name = Path(unquote(path)).name
    suffix = Path(name).suffix.lower()
    if not suffix or suffix == ".":
        return None
    return suffix


def _alias_groups(rules: dict[str, object]) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    raw_aliases = rules.get("extension_aliases", {})
    if not isinstance(raw_aliases, dict):
        return aliases
    for canonical, values in raw_aliases.items():
        c = str(canonical).lower()
        if not c.startswith("."):
            c = f".{c}"
        group = {c}
        if isinstance(values, (list, tuple, set)):
            for value in values:
                ext = str(value).lower()
                if not ext.startswith("."):
                    ext = f".{ext}"
                group.add(ext)
        aliases[c] = group
    return aliases


def equivalent_extensions(extension: str | None, rules: dict[str, object]) -> set[str]:
    """Return the canonical alias family for a file extension."""
    if not extension:
        return set()
    ext = extension.lower()
    result = {ext}
    for canonical, group in _alias_groups(rules).items():
        if ext in group:
            result.update(group)
            result.add(canonical)
    return result


def _detected_extension_family(detection: DetectionResult, rules: dict[str, object]) -> set[str]:
    """Return accepted extension aliases for authoritative byte identity."""
    return equivalent_extensions(detection.canonical_extension, rules)


def validate_download(
    path: Path,
    rules: dict[str, object],
    *,
    source_url: str | None = None,
    content_type: str | None = None,
    content_disposition: str | None = None,
    url_extension: str | None = None,
    filename: str | None = None,
    file_sha256: str | None = None,
) -> ValidationResult:
    """Validate identity, consistency and security policy without trusting the filename."""
    if not path.is_file():
        raise DownloadIngestionError("SECURITY_FILE_NOT_FOUND", f"Download file does not exist: {path}", stage="SECURITY_VALIDATION", operation="validate_download")

    max_size = int(rules.get("max_file_size_mb", 25)) * 1024 * 1024
    size = path.stat().st_size
    if size > max_size:
        raise DownloadIngestionError(
            "SECURITY_FILE_TOO_LARGE",
            f"File is {size} bytes; maximum configured size is {max_size} bytes.",
            stage="SECURITY_VALIDATION",
            operation="validate_download",
        )

    detection = detect_file_type(path, rules)
    original_extension = path.suffix.lower()
    filename_value = filename or path.name
    effective_extension = detection.canonical_extension or (original_extension if detection.kind in {"text", "csv"} else None)
    accepted = {str(item).lower() for item in rules.get("accepted_extensions", [])}
    if detection.kind in {"text", "csv"} and original_extension in accepted:
        effective_extension = original_extension
    canonical_family = _detected_extension_family(detection, rules)

    findings = list(detection.security_findings)
    if detection.kind == "unknown-binary":
        findings.append("SECURITY_UNKNOWN_BINARY")

    # A URL extension is optional. When it exists, it is a consistency signal only.
    if url_extension is None and source_url:
        url_extension = extract_url_extension(source_url)
    url_match: bool | None = None
    if url_extension:
        url_family = equivalent_extensions(url_extension, rules)
        url_match = bool(url_family.intersection(canonical_family))
        if detection.kind in {"text", "csv"} and url_extension in accepted:
            url_match = True
        if not url_match:
            findings.append("SECURITY_URL_TYPE_MISMATCH")

    declared_mime = (content_type or "").split(";", 1)[0].strip().lower() or None
    mime_match: bool | None = None
    if declared_mime and declared_mime not in _GENERIC_MIME_TYPES and effective_extension:
        allowed_mimes = _MIME_CANONICAL.get(effective_extension, set())
        if allowed_mimes:
            mime_match = declared_mime in allowed_mimes
            if not mime_match:
                findings.append("SECURITY_MIME_TYPE_MISMATCH")

    if detection.archive_kind == "docx-macro":
        findings.append("SECURITY_ACTIVE_CONTENT")
    if detection.security_findings:
        findings.extend(detection.security_findings)

    fatal_findings = {
        "SECURITY_PATH_TRAVERSAL",
        "SECURITY_ABSOLUTE_MEMBER_PATH",
        "SECURITY_DRIVE_MEMBER_PATH",
        "SECURITY_SYMLINK_MEMBER",
        "SECURITY_DUPLICATE_MEMBER",
        "SECURITY_WINDOWS_ILLEGAL_FILENAME",
        "SECURITY_FILENAME_TOO_LONG",
        "SECURITY_VBA_PRESENT",
        "SECURITY_VBA_SIGNATURE_PRESENT",
        "SECURITY_ACTIVEX_PRESENT",
        "SECURITY_EXECUTABLE_MEMBER",
        "SECURITY_SCRIPT_MEMBER",
        "SECURITY_ACTIVE_CONTENT",
        "SECURITY_EMBEDDED_ACTIVE_CONTENT",
        "SECURITY_EXTERNAL_RESOURCE_REFERENCE",
        "SECURITY_SVG_ACTIVE_CONTENT",
        "SECURITY_SVG_XML_INVALID",
        "SECURITY_DOCX_REQUIRED_PART_MISSING",
        "SECURITY_ODT_REQUIRED_PART_MISSING",
        "SECURITY_ODT_MIMETYPE_MISMATCH",
        "ARCHIVE_SIZE_TOO_LARGE",
        "ARCHIVE_XML_MEMBER_TOO_LARGE",
        "SECURITY_URL_TYPE_MISMATCH",
        "SECURITY_UNKNOWN_BINARY",
        "SECURITY_MAX_ARCHIVE_DEPTH",
        "SECURITY_MAX_NESTED_ARCHIVES",
    }
    rejected_reason = None
    if any(item in fatal_findings for item in findings):
        first = next(item for item in findings if item in fatal_findings)
        rejected_reason = first

    allowed = bool(canonical_family.intersection(accepted))
    if detection.kind in {"text", "csv"} and original_extension in accepted:
        allowed = True
    if detection.kind == "zip-container" and detection.archive_kind in {"docx", "odt"}:
        allowed = bool(effective_extension and equivalent_extensions(effective_extension, rules).intersection(accepted))

    if not allowed and rejected_reason is None:
        rejected_reason = "SECURITY_UNSUPPORTED_TYPE"

    if rejected_reason:
        return ValidationResult(
            accepted=False,
            reason=rejected_reason,
            detection=detection,
            original_extension=original_extension,
            effective_extension=effective_extension,
            url_extension=url_extension,
            url_match=url_match,
            mime_type=detection.mime_type,
            declared_mime_type=declared_mime,
            mime_match=mime_match,
            content_disposition=content_disposition,
            filename=filename_value,
            file_sha256=file_sha256,
            security_status="rejected",
            security_findings=tuple(dict.fromkeys(findings)),
            validation_strength=detection.validation_strength,
        )

    return ValidationResult(
        accepted=True,
        reason="File identity, structure, security policy and configured acceptance rules passed.",
        detection=detection,
        original_extension=original_extension,
        effective_extension=effective_extension,
        url_extension=url_extension,
        url_match=url_match,
        mime_type=detection.mime_type,
        declared_mime_type=declared_mime,
        mime_match=mime_match,
        content_disposition=content_disposition,
        filename=filename_value,
        file_sha256=file_sha256,
        security_status="accepted",
        security_findings=tuple(dict.fromkeys(findings)),
        validation_strength=detection.validation_strength,
    )
