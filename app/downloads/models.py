from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArchiveEntry:
    """Describe one archive member after bounded inspection."""

    name: str
    compressed_size: int
    uncompressed_size: int
    is_directory: bool
    safe_path: bool
    text_preview: str | None = None
    sha256: str | None = None
    detected_kind: str | None = None
    canonical_extension: str | None = None
    mime_type: str | None = None
    source_archive_sha256: str | None = None
    depth: int = 0
    security_findings: tuple[str, ...] = ()
    processing_status: str = "inspected"


@dataclass(frozen=True)
class DetectionResult:
    """Describe authoritative byte identity plus container/security observations."""

    kind: str
    canonical_extension: str | None
    mime_type: str | None
    signature: str
    archive_entries: tuple[ArchiveEntry, ...] = ()
    archive_kind: str | None = None
    archive_member_previews: dict[str, str] = field(default_factory=dict)
    signature_match: bool = True
    container_match: bool | None = None
    validation_strength: str = "signature"
    security_findings: tuple[str, ...] = ()
    archive_depth: int = 0
    nested_archive_count: int = 0


@dataclass(frozen=True)
class ValidationResult:
    """Describe file identity separately from the security/policy decision."""

    accepted: bool
    reason: str
    detection: DetectionResult
    original_extension: str
    effective_extension: str | None
    url_extension: str | None = None
    url_match: bool | None = None
    mime_type: str | None = None
    declared_mime_type: str | None = None
    mime_match: bool | None = None
    content_disposition: str | None = None
    filename: str | None = None
    file_sha256: str | None = None
    security_status: str = "unknown"
    security_findings: tuple[str, ...] = ()
    validation_strength: str = "signature"


@dataclass(frozen=True)
class DownloadContent:
    """Hold safe extracted content, evidence, metadata, and sanitized image artifacts."""

    text: str
    validation: ValidationResult
    metadata: dict[str, object] = field(default_factory=dict)
    evidence: dict[str, object] = field(default_factory=dict)
    safe_image_artifacts: tuple[bytes, ...] = ()


class DownloadIngestionError(Exception):
    """Represent a safe-ingestion failure with a machine-readable taxonomy."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: object = None,
        stage: str | None = None,
        operation: str | None = None,
        status: str = "FAILED",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
        self.stage = stage
        self.operation = operation
        self.status = status

    def as_error_record(
        self,
        *,
        input_data: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        previous_stage: str | None = None,
        next_stage: str | None = None,
        file_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Return the explicit stage/error contract used by the pipeline."""
        return {
            "pipeline_stage": self.stage,
            "operation": self.operation,
            "status": self.status,
            "error_code": self.code,
            "error_message": str(self),
            "exception_type": type(self).__name__,
            "input": input_data or {},
            "output": output or {},
            "timestamp": __import__("time").time(),
            "duration": None,
            "previous_stage": previous_stage,
            "next_stage": next_stage,
            "file_sha256": file_sha256,
            "details": self.details,
        }
