from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from app.downloads.extract import extract_safe_text
from app.downloads.models import DownloadIngestionError
from app.downloads.security import validate_download
from app.downloads.stages import PipelineStage, StageRecord, StageStatus
from app.downloads.detector import detect_file_type


def _rules() -> dict[str, object]:
    raw = os.environ.get("SCRAPPER_RULES_JSON", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid quarantine rules JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Quarantine rules must be a JSON object.")
    return value


def _worker_schema_version(rules: dict[str, object]) -> str:
    return str(os.environ.get("SCRAPPER_WORKER_SCHEMA_VERSION") or rules.get("quarantine_worker_schema_version", "2.0"))


def _config_hash(rules: dict[str, object]) -> str:
    canonical = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _download_metadata() -> dict[str, object]:
    raw = os.environ.get("SCRAPPER_DOWNLOAD_METADATA", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _jsonable_validation(validation) -> dict[str, object]:
    detection = validation.detection
    return {
        "accepted": validation.accepted,
        "reason": validation.reason,
        "original_extension": validation.original_extension,
        "effective_extension": validation.effective_extension,
        "url_extension": validation.url_extension,
        "url_match": validation.url_match,
        "mime_type": validation.mime_type,
        "declared_mime_type": validation.declared_mime_type,
        "mime_match": validation.mime_match,
        "content_disposition": validation.content_disposition,
        "filename": validation.filename,
        "file_sha256": validation.file_sha256,
        "security_status": validation.security_status,
        "security_findings": list(validation.security_findings),
        "validation_strength": validation.validation_strength,
        "detection": {
            "kind": detection.kind,
            "canonical_extension": detection.canonical_extension,
            "mime_type": detection.mime_type,
            "signature": detection.signature,
            "signature_match": detection.signature_match,
            "container_match": detection.container_match,
            "validation_strength": detection.validation_strength,
            "security_findings": list(detection.security_findings),
            "archive_kind": detection.archive_kind,
            "archive_depth": detection.archive_depth,
            "nested_archive_count": detection.nested_archive_count,
            "archive_entries": [
                {
                    "name": entry.name,
                    "compressed_size": entry.compressed_size,
                    "uncompressed_size": entry.uncompressed_size,
                    "is_directory": entry.is_directory,
                    "safe_path": entry.safe_path,
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
        },
    }


def _record(
    records: list[StageRecord],
    stage: PipelineStage,
    operation: str,
    status: str,
    started: float,
    *,
    sha256: str,
    input_data: dict[str, object] | None = None,
    output: dict[str, object] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    exception_type: str | None = None,
) -> None:
    previous = records[-1].pipeline_stage if records else None
    ordered = list(PipelineStage)
    next_stage = ordered[ordered.index(stage) + 1].value if stage in ordered and ordered.index(stage) + 1 < len(ordered) else None
    records.append(
        StageRecord(
            pipeline_stage=stage.value,
            operation=operation,
            status=status,
            timestamp=time.time(),
            duration_ms=(time.perf_counter() - started) * 1000.0,
            error_code=error_code,
            error_message=error_message,
            exception_type=exception_type,
            input=input_data or {},
            output=output or {},
            previous_stage=previous,
            next_stage=next_stage,
            file_sha256=sha256,
        )
    )


def _stage_report(records: list[StageRecord], first_failure: StageRecord | None) -> dict[str, object]:
    return {
        "failed": first_failure is not None,
        "first_failure": first_failure.as_dict() if first_failure else None,
        "stages": [record.as_dict() for record in records],
    }


def _status_for_error(exc: DownloadIngestionError) -> str:
    status = str(getattr(exc, "status", "FAILED")).upper()
    if status in {item.value for item in StageStatus}:
        return status
    if exc.code.startswith(("FORMAT_", "IMAGE_DECODER_UNAVAILABLE")):
        return StageStatus.UNSUPPORTED.value
    if exc.code.endswith("TIMEOUT"):
        return StageStatus.TIMEOUT.value
    return StageStatus.FAILED.value


def main() -> int:
    """Inspect one untrusted payload entirely inside the disposable quarantine container."""
    source_name = Path(os.environ.get("SCRAPPER_SOURCE_NAME", "download")).name or "download"
    rules = _rules()
    request_metadata = _download_metadata()
    max_bytes = int(rules.get("max_file_size_mb", 25)) * 1024 * 1024
    records: list[StageRecord] = []
    first_failure: StageRecord | None = None

    payload = bytearray()
    digest = hashlib.sha256()
    started = time.perf_counter()
    while True:
        chunk = sys.stdin.buffer.read(64 * 1024)
        if not chunk:
            break
        if len(payload) + len(chunk) > max_bytes:
            digest.update(chunk[: max(0, max_bytes - len(payload))])
            sha = digest.hexdigest()
            record = StageRecord(
                pipeline_stage=PipelineStage.QUARANTINE.value,
                operation="receive_untrusted_bytes",
                status=StageStatus.REJECTED.value,
                timestamp=time.time(),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                error_code="QUARANTINE_FILE_TOO_LARGE",
                error_message="Quarantine worker input exceeded the configured maximum size.",
                input={"source_name": source_name},
                output={"received_bytes": len(payload)},
                previous_stage=None,
                next_stage=PipelineStage.HASH.value,
                file_sha256=sha,
            )
            print(json.dumps({"worker_schema_version": _worker_schema_version(rules), "worker_config_hash": _config_hash(rules), "status": "rejected", "code": record.error_code, "reason": record.error_message, "sha256": sha, "validation": {}, "metadata": {}, "evidence": {}, "stages": {"failed": True, "first_failure": record.as_dict(), "stages": [record.as_dict()]}}, separators=(",", ":")))
            return 0
        payload.extend(chunk)
        digest.update(chunk)

    container_sha256 = digest.hexdigest()
    print(json.dumps({"phase": "received", "sha256": container_sha256, "size": len(payload)}, separators=(",", ":")), flush=True)

    if not payload:
        record = StageRecord(
            pipeline_stage=PipelineStage.QUARANTINE.value,
            operation="receive_untrusted_bytes",
            status=StageStatus.REJECTED.value,
            timestamp=time.time(),
            duration_ms=(time.perf_counter() - started) * 1000.0,
            error_code="QUARANTINE_FILE_EMPTY",
            error_message="Downloaded file is empty.",
            input={"source_name": source_name},
            output={"size": 0},
            file_sha256=container_sha256,
            next_stage=PipelineStage.HASH.value,
        )
        report = {"failed": True, "first_failure": record.as_dict(), "stages": [record.as_dict()]}
        print(json.dumps({"worker_schema_version": _worker_schema_version(rules), "worker_config_hash": _config_hash(rules), "status": "rejected", "code": record.error_code, "reason": record.error_message, "sha256": container_sha256, "validation": {}, "metadata": {}, "evidence": {}, "stages": report}, separators=(",", ":")))
        return 0

    try:
        _record(records, PipelineStage.QUARANTINE, "receive_untrusted_bytes", StageStatus.SUCCESS.value, started, sha256=container_sha256, input_data={"source_name": source_name}, output={"size": len(payload)})
        started = time.perf_counter()
        _record(records, PipelineStage.HASH, "verify_received_sha256", StageStatus.SUCCESS.value, started, sha256=container_sha256, output={"sha256": container_sha256})

        with tempfile.TemporaryDirectory(prefix="scrapper-quarantine-") as temp_dir:
            target = Path(temp_dir) / source_name
            target.write_bytes(payload)

            started = time.perf_counter()
            file_metadata = {
                "source_url": request_metadata.get("source_url"),
                "final_url": request_metadata.get("final_url"),
                "redirect_chain": request_metadata.get("redirect_chain", []),
                "url_extension": request_metadata.get("url_extension"),
                "content_type": request_metadata.get("content_type"),
                "content_disposition": request_metadata.get("content_disposition"),
                "filename": request_metadata.get("filename") or source_name,
                "size": len(payload),
            }
            _record(records, PipelineStage.METADATA, "record_download_metadata", StageStatus.SUCCESS.value, started, sha256=container_sha256, output=file_metadata)

            started = time.perf_counter()
            detection = detect_file_type(target, rules)
            _record(records, PipelineStage.TYPE_DETECTION, "detect_file_type", StageStatus.SUCCESS.value, started, sha256=container_sha256, output={"kind": detection.kind, "canonical_extension": detection.canonical_extension, "validation_strength": detection.validation_strength})

            started = time.perf_counter()
            validation = validate_download(
                target,
                rules,
                source_url=str(request_metadata.get("source_url") or "") or None,
                content_type=str(request_metadata.get("content_type") or "") or None,
                content_disposition=str(request_metadata.get("content_disposition") or "") or None,
                url_extension=request_metadata.get("url_extension"),
                filename=str(request_metadata.get("filename") or source_name),
                file_sha256=container_sha256,
            )
            _record(
                records,
                PipelineStage.SECURITY_VALIDATION,
                "validate_security_policy",
                StageStatus.SUCCESS.value if validation.accepted else StageStatus.REJECTED.value,
                started,
                sha256=container_sha256,
                output={"security_status": validation.security_status, "findings": list(validation.security_findings)},
                error_code=None if validation.accepted else validation.reason,
                error_message=None if validation.accepted else validation.reason,
            )
            if not validation.accepted:
                first_failure = records[-1]
                first_failure.status = StageStatus.REJECTED.value
                report = _stage_report(records, first_failure)
                print(json.dumps({"status": "rejected", "code": validation.reason, "reason": validation.reason, "sha256": container_sha256, "validation": _jsonable_validation(validation), "metadata": file_metadata, "evidence": {}, "stages": report}, ensure_ascii=False, separators=(",", ":")))
                return 0

            started = time.perf_counter()
            consistency_ok = validation.url_extension is None or validation.url_match is True
            _record(
                records,
                PipelineStage.TYPE_CONSISTENCY,
                "compare_url_and_declared_type",
                StageStatus.SUCCESS.value if consistency_ok else StageStatus.REJECTED.value,
                started,
                sha256=container_sha256,
                output={"url_extension": validation.url_extension, "url_match": validation.url_match, "mime_match": validation.mime_match},
            )
            if not consistency_ok:
                first_failure = records[-1]
                report = _stage_report(records, first_failure)
                print(json.dumps({"status": "rejected", "code": "SECURITY_URL_TYPE_MISMATCH", "reason": "URL extension does not match the authoritative detected file type.", "sha256": container_sha256, "validation": _jsonable_validation(validation), "metadata": file_metadata, "evidence": {}, "stages": report}, ensure_ascii=False, separators=(",", ":")))
                return 0

            started = time.perf_counter()
            content = extract_safe_text(target, validation, int(rules.get("max_text_chars", 400000)), rules=rules)
            _record(records, PipelineStage.FORMAT_PROCESSING, "process_validated_format", StageStatus.SUCCESS.value, started, sha256=container_sha256, output={"detected_kind": validation.detection.kind, "archive_kind": validation.detection.archive_kind})
            if validation.detection.archive_kind in {"docx", "odt"}:
                _record(records, PipelineStage.DOCUMENT_TEXT, "extract_document_text_and_embedded_images", StageStatus.SUCCESS.value, started, sha256=container_sha256, output={"text_characters": len(content.text), "embedded_images": len(content.metadata.get("image_analysis", [])) if isinstance(content.metadata.get("image_analysis"), list) else 0})
            elif validation.detection.kind in {"jpeg", "png", "gif", "webp", "bmp", "tiff", "ico", "heif", "avif", "svg"}:
                _record(records, PipelineStage.IMAGE_PROCESSING, "decode_validate_and_normalize_image", StageStatus.SUCCESS.value, started, sha256=container_sha256, output={"safe_artifacts": len(content.safe_image_artifacts)})
                _record(records, PipelineStage.OCR, "ocr_normalized_images", StageStatus.SUCCESS.value, started, sha256=container_sha256, output={"ocr_status": content.evidence.get("ocr", {}).get("status") if isinstance(content.evidence.get("ocr"), dict) else None})
            _record(records, PipelineStage.EVIDENCE_ASSEMBLY, "assemble_structured_evidence", StageStatus.SUCCESS.value, started, sha256=container_sha256, output={"schema_version": content.evidence.get("schema_version")})
            _record(records, PipelineStage.FINALIZATION, "finalize_accepted_artifact", StageStatus.SUCCESS.value, started, sha256=container_sha256, output={"status": "accepted"})

            max_safe_total = int(rules.get("max_safe_image_output_kb", 512)) * 1024
            encoded: list[str] = []
            total = 0
            for artifact in content.safe_image_artifacts:
                if total + len(artifact) > max_safe_total:
                    break
                encoded.append(base64.b64encode(artifact).decode("ascii"))
                total += len(artifact)

            content.metadata.update({
                "worker_security_boundary": "docker",
                "worker_container": True,
                "source_name": source_name,
                "download_metadata": file_metadata,
                "safe_image_artifacts_returned": len(encoded),
                "pipeline_state": _stage_report(records, None),
            })
            result = {
                "worker_schema_version": _worker_schema_version(rules),
                "worker_config_hash": _config_hash(rules),
                "status": "accepted",
                "sha256": container_sha256,
                "text": content.text,
                "metadata": content.metadata,
                "evidence": content.evidence,
                "validation": _jsonable_validation(validation),
                "safe_image_artifacts_b64": encoded,
                "stages": _stage_report(records, None),
            }
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            return 0
    except DownloadIngestionError as exc:
        status = _status_for_error(exc)
        record = StageRecord(
            pipeline_stage=str(exc.stage or PipelineStage.FORMAT_PROCESSING.value),
            operation=str(exc.operation or "worker_processing"),
            status=status,
            timestamp=time.time(),
            error_code=exc.code,
            error_message=str(exc),
            exception_type=type(exc).__name__,
            input={"source_name": source_name},
            output={},
            previous_stage=records[-1].pipeline_stage if records else None,
            next_stage=None,
            file_sha256=container_sha256,
        )
        records.append(record)
        first_failure = record
        print(json.dumps({
            "worker_schema_version": _worker_schema_version(rules),
            "worker_config_hash": _config_hash(rules),
            "status": status.lower(),
            "code": exc.code,
            "reason": str(exc),
            "sha256": container_sha256,
            "details": exc.details,
            "stages": _stage_report(records, first_failure),
        }, ensure_ascii=False, separators=(",", ":")))
        return 0 if status in {"REJECTED", "UNSUPPORTED", "INCONCLUSIVE", "TIMEOUT"} else 2
    except Exception as exc:
        record = StageRecord(
            pipeline_stage=PipelineStage.FORMAT_PROCESSING.value,
            operation="worker_processing",
            status=StageStatus.FAILED.value,
            timestamp=time.time(),
            error_code="QUARANTINE_WORKER_EXCEPTION",
            error_message=f"Worker failed while inspecting the file: {exc}",
            exception_type=type(exc).__name__,
            input={"source_name": source_name},
            output={},
            previous_stage=records[-1].pipeline_stage if records else None,
            next_stage=None,
            file_sha256=container_sha256,
        )
        records.append(record)
        print(json.dumps({"worker_schema_version": _worker_schema_version(rules), "worker_config_hash": _config_hash(rules), "status": "error", "code": record.error_code, "reason": record.error_message, "sha256": container_sha256, "validation": {}, "metadata": {}, "evidence": {}, "stages": _stage_report(records, record)}, ensure_ascii=False, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
