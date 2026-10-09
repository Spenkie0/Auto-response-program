from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .docker_quarantine import DockerQuarantineError, DockerQuarantineRunner
from .downloader import DownloadFetchResult, download_url_bytes
from .models import DownloadContent, DownloadIngestionError
from .rules import load_download_rules


@dataclass(frozen=True)
class DownloadWorkflowResult:
    """Describe one Docker-quarantined download and its extracted safe content."""

    source_path: Path | None
    quarantine_path: Path | None
    processed_path: Path | None
    content: DownloadContent


class DownloadWorkflowError(DownloadIngestionError):
    """Represent a download workflow failure while retaining quarantine/job context."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        quarantine_path: Path | None = None,
        rejected_path: Path | None = None,
        job_id: str | None = None,
        details: object = None,
        stage: str | None = None,
        operation: str | None = None,
        status: str = "FAILED",
    ) -> None:
        super().__init__(code, message, details=details, stage=stage, operation=operation, status=status)
        self.quarantine_path = quarantine_path
        self.rejected_path = rejected_path
        self.job_id = job_id


def _jsonable(value: object) -> object:
    """Convert common dataclass/path values into safe JSON evidence."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict
        return _jsonable(asdict(value))
    return value


def save_processing_metadata(project_root: Path, content: DownloadContent) -> Path:
    """Persist concise accepted-artifact metadata and structured evidence."""
    source_name = str(content.metadata.get("source_name", "download"))
    stem = Path(source_name).stem or "download"
    processed_dir = Path(project_root) / "data" / "downloads" / "metadata"
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / f"{stem}_{content.validation.file_sha256 or 'unknown'}.json"
    payload = _jsonable(dict(content.metadata))
    assert isinstance(payload, dict)
    payload.update(
        {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "validation_reason": content.validation.reason,
            "original_extension": content.validation.original_extension,
            "effective_extension": content.validation.effective_extension,
            "url_extension": content.validation.url_extension,
            "url_match": content.validation.url_match,
            "mime_match": content.validation.mime_match,
            "security_status": content.validation.security_status,
            "security_findings": list(content.validation.security_findings),
            "archive_entries": [entry.name for entry in content.validation.detection.archive_entries],
            "evidence_schema_version": content.evidence.get("schema_version") if content.evidence else None,
        }
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    evidence_dir = Path(project_root) / "data" / "downloads" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"{stem}_{content.validation.file_sha256 or 'unknown'}.json"
    evidence_path.write_text(json.dumps(_jsonable(content.evidence), ensure_ascii=False, indent=2), encoding="utf-8")
    content.metadata["evidence_path"] = str(evidence_path)
    content.metadata["processing_metadata_path"] = str(path)
    return path


def _read_stable_source(source_path: Path, max_bytes: int) -> tuple[bytes, str]:
    """Read one source file and reject changes during transfer into Docker quarantine."""
    source = Path(source_path)
    if not source.is_file():
        raise DownloadWorkflowError("FILE_NOT_FOUND", f"Attachment does not exist: {source}", stage="QUARANTINE", operation="read_source")
    before = source.stat()
    if before.st_size > max_bytes:
        raise DownloadWorkflowError("FILE_TOO_LARGE", f"Attachment exceeds the configured {max_bytes} byte limit.", stage="QUARANTINE", operation="read_source", status="REJECTED")
    payload = source.read_bytes()
    after = source.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or len(payload) != after.st_size:
        raise DownloadWorkflowError(
            "DOWNLOAD_SOURCE_CHANGED",
            "The host-side download changed while it was being transferred into Docker quarantine.",
            stage="QUARANTINE",
            operation="verify_source_stability",
            status="REJECTED",
        )
    return payload, hashlib.sha256(payload).hexdigest()


def process_download_bytes(
    payload: bytes,
    project_root: Path,
    *,
    source_name: str = "download",
    rules: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
    on_hash_verified=None,
) -> DownloadWorkflowResult:
    """Send exact bytes into the Docker-only quarantine boundary."""
    effective_rules = rules or load_download_rules()
    backend = str(effective_rules.get("quarantine_backend", "docker")).lower()
    if backend != "docker":
        raise DownloadWorkflowError(
            "QUARANTINE_BACKEND_INVALID",
            "The production download pipeline only supports the Docker quarantine backend; host-side parsing is disabled.",
            stage="QUARANTINE",
            operation="select_backend",
            status="FAILED",
        )
    max_bytes = int(effective_rules["max_file_size_mb"]) * 1024 * 1024
    if len(payload) > max_bytes:
        raise DownloadWorkflowError("FILE_TOO_LARGE", f"Download exceeds the configured {max_bytes} byte limit.", stage="QUARANTINE", operation="accept_bytes", status="REJECTED")

    runner = DockerQuarantineRunner(project_root, effective_rules)
    try:
        result = runner.process_bytes(
            payload,
            source_name=source_name,
            metadata=metadata,
            on_hash_verified=on_hash_verified,
        )
    except DockerQuarantineError as exc:
        raise DownloadWorkflowError(
            exc.code,
            str(exc),
            quarantine_path=Path(str(exc.details.get("quarantine_path"))) if isinstance(exc.details, dict) and exc.details.get("quarantine_path") else None,
            job_id=(exc.details or {}).get("job_id") if isinstance(exc.details, dict) else None,
            details=getattr(exc, "details", None),
            stage=getattr(exc, "stage", None),
            operation=getattr(exc, "operation", None),
            status=getattr(exc, "status", "FAILED"),
        ) from exc

    content = result.content
    content.metadata.update(
        {
            "source_name": source_name,
            "source_sha256": result.host_sha256,
            "processed_path": None,
            "quarantine_path": str(result.quarantine_path) if result.quarantine_path else None,
            "quarantine_sidecar": content.metadata.get("quarantine_sidecar"),
            "host_source_deleted": False,
        }
    )
    save_processing_metadata(project_root, content)
    return DownloadWorkflowResult(
        source_path=None,
        quarantine_path=result.quarantine_path,
        processed_path=None,
        content=content,
    )


def process_download(
    source_path: Path,
    project_root: Path,
    *,
    rules: dict[str, object] | None = None,
    delete_source_after_handoff: bool = False,
) -> DownloadWorkflowResult:
    """Securely transfer a host-side download into Docker without parsing it on Windows."""
    effective_rules = rules or load_download_rules()
    max_bytes = int(effective_rules["max_file_size_mb"]) * 1024 * 1024
    source = Path(source_path)
    payload, host_sha256 = _read_stable_source(source, max_bytes)
    source_deleted = False

    def _delete_source_after_verified_hash() -> None:
        nonlocal source_deleted
        if not delete_source_after_handoff:
            return
        source.unlink(missing_ok=False)
        source_deleted = True

    try:
        result = process_download_bytes(
            payload,
            project_root,
            source_name=source.name,
            rules=effective_rules,
            metadata={"source_name": source.name, "host_source_sha256": host_sha256},
            on_hash_verified=_delete_source_after_verified_hash,
        )
        result.content.metadata.update(
            {
                "host_source_sha256": host_sha256,
                "host_source_path": str(source),
                "host_source_deleted": source_deleted,
                "host_source_delete_stage": "after_container_hash_verification" if source_deleted else None,
            }
        )
        save_processing_metadata(project_root, result.content)
        return DownloadWorkflowResult(
            source_path=source,
            quarantine_path=result.quarantine_path,
            processed_path=None,
            content=result.content,
        )
    except DownloadWorkflowError:
        raise
    except OSError as exc:
        raise DownloadWorkflowError(
            "DOWNLOAD_CLEANUP_FAILED",
            f"The downloaded file could not be removed after quarantine transfer: {exc}",
            details={"source_path": str(source), "host_sha256": host_sha256},
            stage="CLEANUP",
            operation="delete_source_after_handoff",
        ) from exc


def download_and_process(
    url: str,
    project_root: Path,
    *,
    page_url: str = "",
    rules: dict[str, object] | None = None,
) -> tuple[DownloadWorkflowResult, DownloadFetchResult]:
    """Download into memory, record network provenance, then stream exact bytes into Docker."""
    effective_rules = rules or load_download_rules()
    fetched = download_url_bytes(
        url,
        page_url=page_url,
        max_size_mb=int(effective_rules["max_file_size_mb"]),
        timeout=int(effective_rules.get("download_timeout_seconds", 30)),
        same_origin_only=bool(effective_rules.get("download_same_origin_only", True)),
        allowed_hosts=set(effective_rules.get("download_allowed_hosts", [])),
        max_redirects=int(effective_rules.get("download_max_redirects", 5)),
        user_agent=str(effective_rules.get("download_user_agent", "Scrapper-Ollama/2.0")),
        allow_private_ips=bool(effective_rules.get("download_allow_private_ips", False)),
        allowed_ip_ranges=list(effective_rules.get("download_allowed_ip_ranges", [])),
        max_header_bytes=int(effective_rules.get("download_max_header_bytes", 65536)),
        allowed_schemes={str(item).lower() for item in effective_rules.get("download_allowed_schemes", ["http", "https"])},
        dns_pin=bool(effective_rules.get("download_dns_pin", True)),
        max_url_length=int(effective_rules.get("download_max_url_length", 4096)),
    )
    source_name = fetched.filename or Path(urlsplit(fetched.final_url).path).name or Path(urlsplit(url).path).name or "download"
    metadata = {
        "source_url": fetched.source_url,
        "final_url": fetched.final_url,
        "redirect_chain": list(fetched.redirect_chain),
        "url_extension": fetched.url_extension,
        "content_type": fetched.content_type,
        "content_disposition": fetched.content_disposition,
        "filename": fetched.filename or source_name,
        "download_size": fetched.size,
        "download_sha256": fetched.sha256,
    }
    result = process_download_bytes(
        fetched.payload or b"",
        project_root,
        source_name=source_name,
        rules=effective_rules,
        metadata=metadata,
    )
    result.content.metadata.update(metadata)
    save_processing_metadata(project_root, result.content)
    return result, fetched


def delete_quarantined_download(attachment_metadata: dict[str, object]) -> bool:
    """Delete a retained quarantine artifact only when it is inside the managed quarantine directory."""
    quarantine_value = attachment_metadata.get("quarantine_path")
    if not quarantine_value:
        return False
    target = Path(str(quarantine_value)).resolve()
    root = target.parent.resolve()
    if target.suffix != ".bin" or root.name != "quarantine":
        raise DownloadIngestionError("CLEANUP_POLICY_VIOLATION", "Refusing to delete a quarantine artifact outside the managed quarantine directory.", stage="CLEANUP", operation="delete_quarantine_artifact", status="FAILED")
    if not target.exists():
        return False
    if not target.is_file():
        raise DownloadIngestionError("DOWNLOAD_CLEANUP_FAILED", f"Quarantine artifact is not a regular file: {target}", stage="CLEANUP", operation="delete_quarantine_artifact", status="FAILED")
    target.unlink()
    sidecar = target.with_suffix(".json")
    sidecar.unlink(missing_ok=True)
    return True
