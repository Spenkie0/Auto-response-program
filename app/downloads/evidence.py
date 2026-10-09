from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import ValidationResult

EVIDENCE_SCHEMA_VERSION = "2.0"


def empty_evidence() -> dict[str, object]:
    """Return the stable evidence interface with explicit non-existent categories."""
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "lineage": {
            "status": "non-existent",
            "artifact_id": None,
            "source_sha256": None,
            "producer_stage": None,
            "producer_version": None,
            "pipeline_version": None,
            "config_hash": None,
        },
        "file": {
            "status": "non-existent",
            "name": None,
            "extension": None,
            "url_extension": None,
            "url_extension_match": None,
            "detected_kind": None,
            "canonical_extension": None,
            "mime_type": None,
            "declared_mime_type": None,
            "mime_match": None,
            "size_bytes": None,
            "sha256": None,
            "signature_match": None,
            "container_match": None,
            "validation_strength": None,
            "content_disposition": None,
        },
        "metadata": {
            "status": "non-existent",
            "title": None,
            "author": None,
            "created_at": None,
            "modified_at": None,
            "software": None,
            "gps": None,
            "custom_properties": None,
        },
        "structure": {
            "status": "non-existent",
            "container_type": None,
            "pages": None,
            "sheets": None,
            "sections": None,
            "relationships": None,
            "entries": None,
        },
        "text": {
            "status": "non-existent",
            "content": None,
            "language": None,
            "character_count": None,
        },
        "tables": {
            "status": "non-existent",
            "count": None,
            "items": None,
        },
        "images": {
            "status": "non-existent",
            "count": None,
            "items": None,
        },
        "ocr": {
            "status": "non-existent",
            "text": None,
            "items": None,
        },
        "visual_analysis": {
            "status": "non-existent",
            "items": None,
        },
        "embedded_content": {
            "status": "non-existent",
            "count": None,
            "items": None,
        },
        "security": {
            "status": "non-existent",
            "status_value": None,
            "findings": None,
            "macros": None,
            "active_content": None,
            "embedded_executables": None,
            "path_traversal": None,
            "archive_entries": None,
        },
    }


def _set(section: dict[str, object], **values: object) -> None:
    section.update(values)
    section["status"] = "present"


def _embedded_images(validation: ValidationResult, source_name: str) -> list[dict[str, object]]:
    """Describe validated image artifacts by source, including archive-member provenance."""
    detection = validation.detection
    items: list[dict[str, object]] = []
    if detection.kind in {"jpeg", "png", "gif", "webp", "bmp", "tiff", "ico", "heif", "avif", "svg"}:
        items.append({
            "name": source_name,
            "extension": detection.canonical_extension,
            "mime_type": detection.mime_type,
            "source": "file",
            "sha256": validation.file_sha256,
        })
    for entry in detection.archive_entries:
        if entry.is_directory:
            continue
        suffix = Path(entry.name).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico", ".heif", ".heic", ".avif"}:
            items.append({
                "name": entry.name,
                "extension": suffix or None,
                "mime_type": entry.mime_type,
                "source": "embedded",
                "size_bytes": entry.uncompressed_size,
                "sha256": entry.sha256,
                "source_archive_sha256": entry.source_archive_sha256,
                "depth": entry.depth,
                "security_findings": list(entry.security_findings),
            })
    return items


def build_evidence(
    path: Path,
    validation: ValidationResult,
    *,
    extracted_text: str | None = None,
    sha256: str | None = None,
    metadata_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the canonical evidence object after validation has completed."""
    evidence = empty_evidence()
    detection = validation.detection
    size = path.stat().st_size if path.exists() else None
    overrides = metadata_overrides or {}

    file_section = evidence["file"]
    assert isinstance(file_section, dict)
    _set(
        file_section,
        name=validation.filename or path.name,
        extension=validation.effective_extension or validation.original_extension or None,
        url_extension=validation.url_extension,
        url_extension_match=validation.url_match,
        detected_kind=detection.kind,
        canonical_extension=detection.canonical_extension,
        mime_type=detection.mime_type,
        declared_mime_type=validation.declared_mime_type,
        mime_match=validation.mime_match,
        size_bytes=size,
        sha256=sha256 or validation.file_sha256,
        signature_match=detection.signature_match,
        container_match=detection.container_match,
        validation_strength=validation.validation_strength,
        content_disposition=validation.content_disposition,
    )

    lineage = evidence["lineage"]
    assert isinstance(lineage, dict)
    _set(
        lineage,
        artifact_id=overrides.get("artifact_id") or (sha256 or validation.file_sha256),
        source_sha256=sha256 or validation.file_sha256,
        producer_stage="EVIDENCE_ASSEMBLY",
        producer_version=str(overrides.get("producer_version") or "download-evidence-2"),
        pipeline_version=str(overrides.get("pipeline_version") or "download-pipeline-2"),
        config_hash=overrides.get("config_hash"),
    )

    metadata = evidence["metadata"]
    assert isinstance(metadata, dict)
    _set(
        metadata,
        title=overrides.get("title"),
        author=overrides.get("author"),
        created_at=overrides.get("created_at"),
        modified_at=overrides.get("modified_at"),
        software=overrides.get("software"),
        gps=overrides.get("gps"),
        custom_properties=overrides.get("custom_properties"),
    )

    structure = evidence["structure"]
    assert isinstance(structure, dict)
    if detection.archive_entries or detection.archive_kind:
        _set(
            structure,
            container_type=detection.archive_kind or "zip-container",
            pages=None,
            sheets=None,
            sections=None,
            relationships=overrides.get("relationships"),
            entries=[
                {
                    "name": entry.name,
                    "compressed_size": entry.compressed_size,
                    "uncompressed_size": entry.uncompressed_size,
                    "is_directory": entry.is_directory,
                    "sha256": entry.sha256,
                    "detected_kind": entry.detected_kind,
                    "canonical_extension": entry.canonical_extension,
                    "mime_type": entry.mime_type,
                    "source_archive_sha256": entry.source_archive_sha256,
                    "depth": entry.depth,
                    "security_findings": list(entry.security_findings),
                    "processing_status": entry.processing_status,
                }
                for entry in detection.archive_entries
            ],
        )

    if extracted_text is not None:
        text = evidence["text"]
        assert isinstance(text, dict)
        _set(text, content=extracted_text, language=None, character_count=len(extracted_text))
        if detection.kind == "csv":
            tables = evidence["tables"]
            assert isinstance(tables, dict)
            lines = [line for line in extracted_text.splitlines() if line.strip()]
            _set(tables, count=1, items=[{"row_count": max(0, len(lines) - 1)}])

    images = _embedded_images(validation, validation.filename or path.name)
    analysis = overrides.get("image_analysis")
    if isinstance(analysis, list):
        by_name = {str(item.get("name")): item for item in analysis if isinstance(item, dict)}
        for item in images:
            if item.get("name") in by_name:
                item["analysis"] = by_name[item["name"]]
    if images:
        image_section = evidence["images"]
        assert isinstance(image_section, dict)
        _set(image_section, count=len(images), items=images)

    embedded = [
        entry.name for entry in detection.archive_entries
        if not entry.is_directory and entry.name.lower().replace("\\", "/").startswith(("word/embeddings/", "xl/embeddings/", "ppt/embeddings/"))
    ]
    if embedded:
        embedded_section = evidence["embedded_content"]
        assert isinstance(embedded_section, dict)
        _set(embedded_section, count=len(embedded), items=embedded)

    security = evidence["security"]
    assert isinstance(security, dict)
    findings = list(dict.fromkeys((*detection.security_findings, *validation.security_findings)))
    _set(
        security,
        status_value=validation.security_status,
        findings=findings,
        macros=any(item.startswith("SECURITY_VBA") for item in findings),
        active_content=any(item in findings for item in {"SECURITY_ACTIVE_CONTENT", "SECURITY_ACTIVEX_PRESENT", "SECURITY_SCRIPT_MEMBER", "SECURITY_SVG_ACTIVE_CONTENT"}),
        embedded_executables=any(item == "SECURITY_EXECUTABLE_MEMBER" for item in findings),
        path_traversal=any(item in findings for item in {"SECURITY_PATH_TRAVERSAL", "SECURITY_ABSOLUTE_MEMBER_PATH", "SECURITY_DRIVE_MEMBER_PATH"}),
        archive_entries=len(detection.archive_entries) if detection.archive_entries else None,
    )
    if overrides.get("ocr"):
        ocr = evidence["ocr"]
        if isinstance(ocr, dict):
            _set(ocr, **dict(overrides["ocr"]))

    return evidence


def merge_evidence(base: dict[str, object] | None, updates: dict[str, object] | None) -> dict[str, object]:
    """Merge stage outputs while preserving the stable top-level evidence schema."""
    target = empty_evidence()
    if isinstance(base, dict):
        target.update(base)
    if not isinstance(updates, dict):
        return target
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key] = {**target[key], **value}  # type: ignore[arg-type]
        else:
            target[key] = value
    return target
