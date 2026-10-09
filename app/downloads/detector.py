from __future__ import annotations

import csv
import hashlib
from io import BytesIO
import posixpath
from pathlib import PurePosixPath
import re
import stat
from pathlib import Path
import time
import unicodedata
import zipfile

from defusedxml import ElementTree as SafeET

from .models import ArchiveEntry, DetectionResult, DownloadIngestionError

_SIGNATURES: list[tuple[bytes, str, str | None, str | None]] = [
    (b"MZ", "pe", ".exe", "application/vnd.microsoft.portable-executable"),
    (b"%PDF-", "pdf", ".pdf", "application/pdf"),
    (b"Rar!\x1a\x07\x00", "rar", ".rar", "application/vnd.rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z", ".7z", "application/x-7z-compressed"),
    (b"\x1f\x8b", "gzip", ".gz", "application/gzip"),
    (b"\x7fELF", "elf", ".elf", "application/x-executable"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole", ".ole", "application/vnd.ms-compound-document"),
    (b"\xff\xd8\xff", "jpeg", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", ".png", "image/png"),
    (b"GIF87a", "gif", ".gif", "image/gif"),
    (b"GIF89a", "gif", ".gif", "image/gif"),
    (b"BM", "bmp", ".bmp", "image/bmp"),
    (b"II*\x00", "tiff", ".tif", "image/tiff"),
    (b"MM\x00*", "tiff", ".tiff", "image/tiff"),
    (b"\x00\x00\x01\x00", "ico", ".ico", "image/x-icon"),
]

_TEXT_LIKE_EXTENSIONS = {".txt", ".xml", ".csv", ".json", ".rels", ".ini", ".cfg", ".md", ".css"}
_EXECUTABLE_SUFFIXES = {".exe", ".dll", ".scr", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".msi", ".hta"}
_ACTIVE_CONTENT_MARKERS = {
    ".js": "SECURITY_SCRIPT_MEMBER",
    ".vbs": "SECURITY_SCRIPT_MEMBER",
    ".ps1": "SECURITY_SCRIPT_MEMBER",
    ".bat": "SECURITY_SCRIPT_MEMBER",
    ".cmd": "SECURITY_SCRIPT_MEMBER",
    ".hta": "SECURITY_ACTIVE_CONTENT",
}
_FORBIDDEN_DOCUMENT_MEMBERS = {
    "word/vbaproject.bin": "SECURITY_VBA_PRESENT",
    "word/vbaprojectsignature.bin": "SECURITY_VBA_SIGNATURE_PRESENT",
}
_PREVIEW_LIMIT = 4096
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_ILLEGAL_RE = re.compile(r"[<>:\"|?*]")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def read_signature(path: Path, size: int = 64) -> bytes:
    """Read the first bytes of a file for signature inspection."""
    with path.open("rb") as handle:
        return handle.read(size)


def _looks_like_text(raw: bytes) -> bool:
    """Return whether bytes look like ordinary text rather than binary data."""
    sample = raw[:65536]
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    for encoding in ("utf-8", "utf-16"):
        try:
            sample.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


def _looks_like_csv(raw: bytes) -> bool:
    """Conservatively recognize delimiter-based text with at least two rows."""
    try:
        text = raw[:65536].decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    rows = [line for line in text.splitlines() if line.strip()]
    if len(rows) < 2:
        return False
    try:
        dialect = csv.Sniffer().sniff("\n".join(rows[:10]), delimiters=",;\t|")
    except csv.Error:
        return False
    counts = [line.count(dialect.delimiter) for line in rows[:10]]
    return bool(counts and all(count > 0 for count in counts[: min(len(counts), 5)]))


def _canonical_member_name(name: str) -> str:
    """Normalize an archive name for security comparison without using it as a host path."""
    normalized = unicodedata.normalize("NFC", str(name).replace("\\", "/"))
    return posixpath.normpath(normalized)


def _member_name_findings(name: str, *, is_symlink: bool = False, max_name_chars: int = 260) -> tuple[str, ...]:
    """Return Windows-aware archive-name security findings."""
    findings: list[str] = []
    normalized = unicodedata.normalize("NFC", str(name).replace("\\", "/"))
    pure = PurePosixPath(normalized)
    parts = pure.parts
    if not normalized or normalized.startswith("/") or normalized.startswith("//"):
        findings.append("SECURITY_ABSOLUTE_MEMBER_PATH")
    if re.match(r"^[A-Za-z]:", normalized):
        findings.append("SECURITY_DRIVE_MEMBER_PATH")
    if ".." in parts:
        findings.append("SECURITY_PATH_TRAVERSAL")
    if _CONTROL_RE.search(normalized):
        findings.append("SECURITY_CONTROL_CHARACTER_FILENAME")
    if any(_WINDOWS_ILLEGAL_RE.search(part) for part in parts):
        findings.append("SECURITY_WINDOWS_ILLEGAL_FILENAME")
    if len(normalized) > max_name_chars:
        findings.append("SECURITY_FILENAME_TOO_LONG")
    for part in parts:
        stem = part.rstrip(". ").split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            findings.append("SECURITY_WINDOWS_RESERVED_FILENAME")
            break
    if is_symlink:
        findings.append("SECURITY_SYMLINK_MEMBER")
    return tuple(dict.fromkeys(findings))


def _is_safe_member_name(name: str, *, max_name_chars: int = 260) -> bool:
    """Return whether an archive member name has no known traversal/name hazards."""
    return not _member_name_findings(name, max_name_chars=max_name_chars)


def _safe_text_preview_bytes(raw: bytes, filename: str) -> str | None:
    """Decode only a small text-like member preview."""
    if not raw or len(raw) > _PREVIEW_LIMIT:
        return None
    suffix = PurePosixPath(filename).suffix.lower()
    basename = PurePosixPath(filename.lower()).name
    if suffix not in _TEXT_LIKE_EXTENSIONS and not basename.endswith((".xml", ".rels")) and basename not in {"mimetype"}:
        return None
    if b"\x00" in raw:
        return None
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)[:_PREVIEW_LIMIT]
        except UnicodeDecodeError:
            continue
    return None


def _office_container_kind(names_lower: set[str], previews: dict[str, str]) -> tuple[str | None, tuple[str, ...]]:
    """Identify a supported package and return format-specific security findings."""
    findings: list[str] = []
    if "word/document.xml" in names_lower:
        required = {"[content_types].xml", "_rels/.rels", "word/document.xml"}
        missing = sorted(required - names_lower)
        if missing:
            findings.append("SECURITY_DOCX_REQUIRED_PART_MISSING")
        content_types = previews.get("[content_types].xml", "").lower()
        if "macroenabled.main+xml" in content_types or any(name in names_lower for name in _FORBIDDEN_DOCUMENT_MEMBERS):
            findings.append("SECURITY_VBA_PRESENT")
        if any(name.startswith("word/activex/") for name in names_lower):
            findings.append("SECURITY_ACTIVEX_PRESENT")
        if any(PurePosixPath(name).suffix.lower() in _EXECUTABLE_SUFFIXES for name in names_lower):
            findings.append("SECURITY_EXECUTABLE_MEMBER")
        if any(name.startswith(("word/vbaData/", "word/activeX/", "word/embeddings/")) for name in names_lower):
            findings.append("SECURITY_EMBEDDED_ACTIVE_CONTENT")
        return "docx" if not findings or findings == ["SECURITY_EXTERNAL_RESOURCE_REFERENCE"] else "docx-macro" if "SECURITY_VBA_PRESENT" in findings else "docx", tuple(dict.fromkeys(findings))

    if "content.xml" in names_lower and "mimetype" in names_lower:
        required = {"mimetype", "content.xml", "meta.xml"}
        missing = sorted(required - names_lower)
        if missing:
            findings.append("SECURITY_ODT_REQUIRED_PART_MISSING")
        mimetype = previews.get("mimetype", "").strip()
        if mimetype != "application/vnd.oasis.opendocument.text":
            findings.append("SECURITY_ODT_MIMETYPE_MISMATCH")
        if any(PurePosixPath(name).suffix.lower() in _EXECUTABLE_SUFFIXES for name in names_lower):
            findings.append("SECURITY_EXECUTABLE_MEMBER")
        if any(PurePosixPath(name).suffix.lower() in {".js", ".xml"} and name.startswith(("Basic/", "Scripts/")) for name in names_lower):
            findings.append("SECURITY_SCRIPT_MEMBER")
        return "odt", tuple(dict.fromkeys(findings))
    return None, tuple()


class _InspectionContext:
    """Carry resource limits across recursive archive inspection."""

    def __init__(self, rules: dict[str, object], *, deadline: float | None = None) -> None:
        self.rules = rules
        timeout = float(rules.get("max_processing_time_seconds", 90))
        self.deadline = deadline if deadline is not None else time.monotonic() + timeout
        self.total_extracted = 0
        self.total_members = 0
        self.nested_archives = 0

    def check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise DownloadIngestionError("ARCHIVE_PROCESSING_TIMEOUT", "Archive inspection exceeded the configured processing time.", status="TIMEOUT")

    def consume(self, size: int) -> None:
        self.check_deadline()
        self.total_extracted += size
        max_total = int(self.rules.get("max_total_extracted_size_mb", self.rules.get("max_uncompressed_size_mb", 100))) * 1024 * 1024
        if self.total_extracted > max_total:
            raise DownloadIngestionError("ARCHIVE_TOTAL_EXTRACTED_TOO_LARGE", "Total recursively extracted archive member data exceeds the configured limit.")


def _member_sha256_and_bytes(archive: zipfile.ZipFile, info: zipfile.ZipInfo, context: _InspectionContext) -> tuple[bytes, str]:
    """Read one bounded member without extracting it to the host filesystem."""
    max_member = int(context.rules.get("max_member_size_mb", context.rules.get("max_archive_member_size_mb", 50))) * 1024 * 1024
    expected = int(info.file_size)
    if expected > max_member:
        raise DownloadIngestionError("ARCHIVE_MEMBER_TOO_LARGE", f"Archive member is too large: {info.filename}")
    if info.flag_bits & 0x1:
        raise DownloadIngestionError("ARCHIVE_ENCRYPTED_MEMBER", f"Encrypted archive members are not supported: {info.filename}")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    read_total = 0
    try:
        with archive.open(info, "r") as handle:
            while True:
                context.check_deadline()
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                read_total += len(chunk)
                if read_total > max_member:
                    raise DownloadIngestionError("ARCHIVE_MEMBER_TOO_LARGE", f"Archive member exceeded the configured limit while decompressed: {info.filename}")
                digest.update(chunk)
                chunks.append(chunk)
    except RuntimeError as exc:
        raise DownloadIngestionError("ARCHIVE_MEMBER_READ_FAILED", f"Could not safely read archive member {info.filename!r}.", details=str(exc)) from exc
    if read_total != expected:
        raise DownloadIngestionError("ARCHIVE_MEMBER_SIZE_MISMATCH", f"Archive member size did not match its declared size: {info.filename}")
    context.consume(read_total)
    return b"".join(chunks), digest.hexdigest()


def _detect_non_archive_bytes(raw: bytes) -> tuple[str, str | None, str | None, str, str]:
    """Identify non-container bytes from signatures and conservative text checks."""
    if not raw:
        return "text", ".txt", "text/plain", "empty-file", "strong"
    for signature, kind, extension, mime in _SIGNATURES:
        if raw.startswith(signature):
            return kind, extension, mime, raw[:16].hex(" "), "strong"
    if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return "webp", ".webp", "image/webp", raw[:16].hex(" "), "strong"
    if len(raw) >= 12 and raw[4:8] == b"ftyp":
        brand = raw[8:12]
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "heif", ".heif", "image/heif", raw[:16].hex(" "), "strong"
        if brand in {b"avif", b"avis"}:
            return "avif", ".avif", "image/avif", raw[:16].hex(" "), "strong"
    if _looks_like_text(raw):
        text_probe = raw[:8192].lstrip(b"\xef\xbb\xbf \t\r\n")
        if text_probe.startswith(b"<svg") or b"<svg" in text_probe[:2048]:
            return "svg", ".svg", "image/svg+xml", raw[:16].hex(" "), "structural"
        kind = "csv" if _looks_like_csv(raw) else "text"
        extension = ".csv" if kind == "csv" else ".txt"
        return kind, extension, "text/csv" if kind == "csv" else "text/plain", raw[:16].hex(" "), "text-heuristic"
    return "unknown-binary", None, None, raw[:16].hex(" "), "weak"


def inspect_zip_bytes(
    raw: bytes,
    rules: dict[str, object],
    *,
    source_archive_sha256: str | None = None,
    depth: int = 0,
    context: _InspectionContext | None = None,
    member_prefix: str = "",
) -> tuple[tuple[ArchiveEntry, ...], str | None, dict[str, str], tuple[str, ...], int]:
    """Inspect ZIP members recursively with bounded resources and per-member provenance."""
    context = context or _InspectionContext(rules)
    context.check_deadline()
    max_archive_size = int(rules.get("max_archive_size_mb", rules.get("max_file_size_mb", 25))) * 1024 * 1024
    if len(raw) > max_archive_size:
        raise DownloadIngestionError("ARCHIVE_SIZE_TOO_LARGE", f"Archive size exceeds the configured {max_archive_size} byte limit.", stage="TYPE_DETECTION", operation="validate_archive_size", status="REJECTED")
    max_depth = int(rules.get("max_archive_depth", 3))
    if depth > max_depth:
        raise DownloadIngestionError("ARCHIVE_NESTING_DEPTH", f"Archive nesting exceeds the configured maximum depth of {max_depth}.")
    try:
        archive = zipfile.ZipFile(BytesIO(raw), "r")
    except zipfile.BadZipFile as exc:
        raise DownloadIngestionError("INVALID_ZIP", "The file has a ZIP signature but is not a valid ZIP archive.") from exc

    entries: list[ArchiveEntry] = []
    previews: dict[str, str] = {}
    names: set[str] = set()
    names_lower: set[str] = set()
    security_findings: list[str] = []
    max_entries = int(rules.get("max_member_count", rules.get("max_archive_entries", 1000)))
    max_ratio = float(rules.get("max_compression_ratio", 100))
    local_total = 0
    nested_count = 0
    archive_sha = source_archive_sha256 or hashlib.sha256(raw).hexdigest()

    try:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise DownloadIngestionError("ARCHIVE_TOO_MANY_ENTRIES", f"Archive contains {len(infos)} entries; maximum is {max_entries}.")
        for info in infos:
            context.check_deadline()
            context.total_members += 1
            if context.total_members > int(rules.get("max_total_member_count", max_entries)):
                raise DownloadIngestionError("ARCHIVE_TOTAL_MEMBER_COUNT", "Recursive archive member count exceeds the configured limit.")

            normalized = _canonical_member_name(info.filename)
            canonical_key = normalized.casefold()
            symlink = stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)
            findings = list(_member_name_findings(info.filename, is_symlink=symlink))
            if canonical_key in names:
                findings.append("SECURITY_DUPLICATE_MEMBER")
            names.add(canonical_key)
            names_lower.add(normalized.lower())
            if findings:
                security_findings.extend(findings)
                fatal_name = next(
                    (item for item in findings if item in {
                        "SECURITY_PATH_TRAVERSAL",
                        "SECURITY_ABSOLUTE_MEMBER_PATH",
                        "SECURITY_DRIVE_MEMBER_PATH",
                        "SECURITY_SYMLINK_MEMBER",
                        "SECURITY_DUPLICATE_MEMBER",
                        "SECURITY_WINDOWS_RESERVED_FILENAME",
                        "SECURITY_CONTROL_CHARACTER_FILENAME",
                        "SECURITY_WINDOWS_ILLEGAL_FILENAME",
                        "SECURITY_FILENAME_TOO_LONG",
                    }),
                    None,
                )
                if fatal_name:
                    raise DownloadIngestionError(
                        "ARCHIVE_UNSAFE_PATH" if fatal_name in {"SECURITY_PATH_TRAVERSAL", "SECURITY_ABSOLUTE_MEMBER_PATH", "SECURITY_DRIVE_MEMBER_PATH"} else fatal_name,
                        f"Archive member name violates the security policy: {info.filename!r}",
                        details={"member": info.filename, "finding": fatal_name},
                        stage="TYPE_DETECTION",
                        operation="inspect_archive_member_name",
                        status="REJECTED",
                    )

            if info.file_size < 0 or info.compress_size < 0:
                raise DownloadIngestionError("ARCHIVE_METADATA_INVALID", f"Archive metadata is invalid for member: {info.filename}")
            if info.file_size > int(rules.get("max_member_size_mb", rules.get("max_archive_member_size_mb", 50))) * 1024 * 1024:
                raise DownloadIngestionError("ARCHIVE_MEMBER_TOO_LARGE", f"Archive member is too large: {info.filename}")
            suffix_lower = PurePosixPath(info.filename).suffix.lower()
            if suffix_lower in {".xml", ".rels"} and info.file_size > int(rules.get("max_xml_bytes", 10_000_000)):
                raise DownloadIngestionError("ARCHIVE_XML_MEMBER_TOO_LARGE", f"XML package member exceeds the configured XML size limit: {info.filename}", stage="TYPE_DETECTION", operation="validate_xml_member_size", status="REJECTED")
            if info.compress_size > 0 and info.file_size / info.compress_size > max_ratio:
                raise DownloadIngestionError("ARCHIVE_COMPRESSION_RATIO", f"Archive member has an excessive compression ratio: {info.filename}")
            local_total += info.file_size
            if local_total > int(rules.get("max_archive_uncompressed_size_mb", rules.get("max_uncompressed_size_mb", 100))) * 1024 * 1024:
                raise DownloadIngestionError("ARCHIVE_TOO_LARGE", "Archive expands beyond the configured archive size limit.")

            if info.is_dir():
                entries.append(
                    ArchiveEntry(
                        name=f"{member_prefix}{info.filename}",
                        compressed_size=info.compress_size,
                        uncompressed_size=info.file_size,
                        is_directory=True,
                        safe_path=not findings,
                        source_archive_sha256=archive_sha,
                        depth=depth,
                        security_findings=tuple(dict.fromkeys(findings)),
                    )
                )
                continue

            member_bytes, member_sha = _member_sha256_and_bytes(archive, info, context)
            kind, extension, mime, signature, strength = _detect_non_archive_bytes(member_bytes[:65536])
            member_nested_count = 0
            member_findings = list(findings)
            member_suffix = PurePosixPath(info.filename).suffix.lower()
            if extension and member_suffix and member_suffix not in {extension}:
                member_findings.append("TYPE_MEMBER_FILENAME_HINT_MISMATCH")

            member_previews = _safe_text_preview_bytes(member_bytes, info.filename)
            if member_previews is not None:
                previews[f"{member_prefix}{normalized}".lower()] = member_previews

            if kind == "unknown-binary" and PurePosixPath(info.filename).suffix.lower() in _EXECUTABLE_SUFFIXES:
                member_findings.append("SECURITY_EXECUTABLE_MEMBER")
            lower_name = normalized.lower()
            suffix = PurePosixPath(lower_name).suffix.lower()
            if suffix in _ACTIVE_CONTENT_MARKERS:
                member_findings.append(_ACTIVE_CONTENT_MARKERS[suffix])
            if lower_name in _FORBIDDEN_DOCUMENT_MEMBERS:
                member_findings.append(_FORBIDDEN_DOCUMENT_MEMBERS[lower_name])
            if lower_name.startswith(("word/activex/", "ppt/activex/", "xl/activex/")):
                member_findings.append("SECURITY_ACTIVEX_PRESENT")
            if any(token in lower_name for token in ("vbaproject", "vbadata", "customui")):
                member_findings.append("SECURITY_VBA_PRESENT")

            child_entries: tuple[ArchiveEntry, ...] = ()
            if kind == "unknown-binary" and member_bytes.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
                if depth + 1 > max_depth:
                    member_findings.append("SECURITY_MAX_ARCHIVE_DEPTH")
                elif context.nested_archives >= int(rules.get("max_nested_archives", 8)):
                    member_findings.append("SECURITY_MAX_NESTED_ARCHIVES")
                else:
                    context.nested_archives += 1
                    nested_count += 1
                    child_entries, child_kind, child_previews, child_findings, child_nested_count = inspect_zip_bytes(
                        member_bytes,
                        rules,
                        source_archive_sha256=member_sha,
                        depth=depth + 1,
                        context=context,
                        member_prefix=f"{member_prefix}{normalized}!/",
                    )
                    entries.extend(child_entries)
                    previews.update(child_previews)
                    member_findings.extend(child_findings)
                    member_nested_count += child_nested_count + 1
                    if child_kind:
                        kind = f"zip:{child_kind}"
                        extension = ".zip"
                        mime = "application/zip"

            entries.append(
                ArchiveEntry(
                    name=f"{member_prefix}{info.filename}",
                    compressed_size=info.compress_size,
                    uncompressed_size=info.file_size,
                    is_directory=False,
                    safe_path=not findings,
                    text_preview=member_previews,
                    sha256=member_sha,
                    detected_kind=kind,
                    canonical_extension=extension,
                    mime_type=mime,
                    source_archive_sha256=archive_sha,
                    depth=depth,
                    security_findings=tuple(dict.fromkeys(member_findings)),
                    processing_status="inspected",
                )
            )
            security_findings.extend(member_findings)
            nested_count += member_nested_count

        archive_kind, format_findings = _office_container_kind(names_lower, previews)
        security_findings.extend(format_findings)
        return tuple(entries), archive_kind, previews, tuple(dict.fromkeys(security_findings)), nested_count
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        if isinstance(exc, DownloadIngestionError):
            raise
        raise DownloadIngestionError("ARCHIVE_MALFORMED", "Archive processing failed while validating its members.", details=str(exc)) from exc
    finally:
        archive.close()


def inspect_zip(path: Path, rules: dict[str, object]) -> tuple[tuple[ArchiveEntry, ...], str | None, dict[str, str]]:
    """Compatibility wrapper returning the bounded recursive ZIP inspection."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DownloadIngestionError("ARCHIVE_READ_FAILED", f"Could not read archive: {exc}") from exc
    entries, kind, previews, findings, _ = inspect_zip_bytes(raw, rules)
    fatal_names = {
        "SECURITY_PATH_TRAVERSAL",
        "SECURITY_ABSOLUTE_MEMBER_PATH",
        "SECURITY_DRIVE_MEMBER_PATH",
        "SECURITY_SYMLINK_MEMBER",
        "SECURITY_DUPLICATE_MEMBER",
        "SECURITY_WINDOWS_RESERVED_FILENAME",
        "SECURITY_CONTROL_CHARACTER_FILENAME",
        "SECURITY_WINDOWS_ILLEGAL_FILENAME",
        "SECURITY_FILENAME_TOO_LONG",
    }
    for finding in findings:
        if finding in fatal_names:
            raise DownloadIngestionError(finding, f"Archive security policy rejected the container: {finding}")
    return entries, kind, previews


def detect_file_type_bytes(raw: bytes, rules: dict[str, object]) -> DetectionResult:
    """Determine actual type from bytes and recursively inspect ZIP containers."""
    if raw.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        entries, archive_kind, previews, findings, nested = inspect_zip_bytes(raw, rules)
        canonical = ".docx" if archive_kind == "docx" else ".odt" if archive_kind == "odt" else ".zip"
        mime = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if archive_kind == "docx"
            else "application/vnd.oasis.opendocument.text"
            if archive_kind == "odt"
            else "application/zip"
        )
        if archive_kind == "docx-macro":
            findings = tuple(dict.fromkeys((*findings, "SECURITY_VBA_PRESENT")))
        return DetectionResult(
            "zip-container",
            canonical,
            mime,
            raw[:4].hex(" "),
            entries,
            archive_kind,
            previews,
            signature_match=True,
            container_match=archive_kind in {"docx", "odt"},
            validation_strength="structural",
            security_findings=tuple(findings),
            archive_depth=0,
            nested_archive_count=nested,
        )
    kind, extension, mime, signature, strength = _detect_non_archive_bytes(raw)
    findings: list[str] = []
    if kind == "svg":
        try:
            root = SafeET.fromstring(raw)
            root_tag = root.tag.rsplit("}", 1)[-1].lower()
            if root_tag != "svg":
                findings.append("SECURITY_SVG_ROOT_INVALID")
            raw_text = raw.decode("utf-8", "ignore").lower()
            if any(token in raw_text for token in ("<script", "onload=", "onerror=", "foreignobject", "href=\"http", "href='http")):
                findings.append("SECURITY_SVG_ACTIVE_CONTENT")
        except Exception as exc:
            findings.append("SECURITY_SVG_XML_INVALID")
    return DetectionResult(
        kind,
        extension,
        mime,
        signature,
        signature_match=kind != "unknown-binary",
        container_match=True,
        validation_strength=strength,
        security_findings=tuple(dict.fromkeys(findings)),
    )


def detect_file_type(path: Path, rules: dict[str, object]) -> DetectionResult:
    """Determine a file's actual type from bytes; parsing stays in the caller's security boundary."""
    if not path.is_file():
        raise DownloadIngestionError("TYPE_FILE_NOT_FOUND", f"Download file does not exist: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DownloadIngestionError("TYPE_READ_FAILED", f"Could not read file for type detection: {exc}") from exc
    return detect_file_type_bytes(raw, rules)
