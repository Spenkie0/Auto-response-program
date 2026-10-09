from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
import queue
import threading
from typing import Any, Callable

from .antimalware import run_antimalware_scan
from .models import ArchiveEntry, DetectionResult, DownloadContent, DownloadIngestionError, ValidationResult
from .stages import PipelineStage, StageStatus


@dataclass(frozen=True)
class DockerQuarantineResult:
    """Describe one disposable-container quarantine job and its extracted result."""

    job_id: str
    image: str
    image_digest: str | None
    host_sha256: str
    container_sha256: str
    source_size: int
    quarantine_path: Path | None
    content: DownloadContent


class DockerQuarantineError(DownloadIngestionError):
    """Represent an isolation, container-runtime, integrity, or policy failure."""


def sha256_bytes(payload: bytes) -> str:
    """Return SHA-256 for an in-memory artifact."""
    return hashlib.sha256(payload).hexdigest()


def _docker_executable() -> str | None:
    return shutil.which("docker") or shutil.which("docker.exe")


def docker_available() -> bool:
    """Return whether the Docker server is reachable."""
    executable = _docker_executable()
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def docker_image_exists(image: str) -> bool:
    """Return whether the configured quarantine image exists."""
    executable = _docker_executable()
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def docker_image_id(image: str) -> str | None:
    """Return the local Docker image ID for reproducibility/provenance."""
    executable = _docker_executable()
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value or None


def build_quarantine_image(project_root: Path, image: str) -> None:
    """Build the reusable quarantine worker image."""
    executable = _docker_executable()
    if not executable:
        raise DockerQuarantineError("QUARANTINE_DOCKER_UNAVAILABLE", "Docker CLI was not found.")
    dockerfile = Path(project_root) / "quarantine" / "Dockerfile"
    if not dockerfile.exists():
        raise DockerQuarantineError("QUARANTINE_DOCKERFILE_MISSING", f"Quarantine Dockerfile not found: {dockerfile}")
    completed = subprocess.run(
        [executable, "build", "--pull", "-f", str(dockerfile), "-t", image, str(project_root)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Docker build failed.").strip()
        raise DockerQuarantineError("QUARANTINE_IMAGE_BUILD_FAILED", detail)


def _rules_for_container(rules: dict[str, object]) -> str:
    """Serialize only configuration needed by the isolated worker."""
    safe_rules: dict[str, object] = {}
    for key, value in rules.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe_rules[key] = value
        elif isinstance(value, (list, tuple)):
            safe_rules[key] = [str(item) for item in value]
        elif isinstance(value, dict):
            safe_rules[key] = {str(k): v for k, v in value.items() if isinstance(v, (str, int, float, bool)) or v is None}
    return json.dumps(safe_rules, ensure_ascii=False, separators=(",", ":"))


def _rules_hash(rules: dict[str, object]) -> str:
    """Hash the exact sanitized rule set sent to the worker for reproducibility."""
    payload = _rules_for_container(rules).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _docker_runtime_security_options() -> list[str]:
    """Return Docker daemon security options used for informational provenance."""
    executable = _docker_executable()
    if not executable:
        return []
    try:
        completed = subprocess.run(
            [executable, "info", "--format", "{{json .SecurityOptions}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            return []
        value = json.loads((completed.stdout or "").strip())
        return [str(item) for item in value] if isinstance(value, list) else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []


def _artifact_paths(project_root: Path, digest: str) -> tuple[Path, Path]:
    """Return byte and provenance paths for a quarantined artifact."""
    directory = Path(project_root) / "data" / "downloads" / "quarantine"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}.bin", directory / f"{digest}.json"


def _persist_quarantine_artifact(
    project_root: Path,
    payload: bytes,
    digest: str,
    *,
    source_name: str,
    metadata: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    """Persist exact untrusted bytes under a hash-only name without opening them."""
    target, sidecar = _artifact_paths(project_root, digest)
    if target.exists():
        existing = hashlib.sha256(target.read_bytes()).hexdigest()
        if existing != digest:
            raise DockerQuarantineError("QUARANTINE_ARTIFACT_HASH_MISMATCH", "Existing quarantine artifact does not match its filename hash.")
    else:
        target.write_bytes(payload)
    record = {
        "schema_version": "1.0",
        "artifact_id": digest,
        "sha256": digest,
        "size": len(payload),
        "source_name": Path(source_name).name or "download",
        "retention_state": "pending",
        "metadata": metadata or {},
    }
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, sidecar


def _deserialize_content(result: dict[str, Any]) -> DownloadContent:
    """Rebuild a normal DownloadContent object from a validated worker JSON result."""
    validation_payload = result.get("validation")
    detection_payload = validation_payload.get("detection") if isinstance(validation_payload, dict) else None
    if not isinstance(validation_payload, dict) or not isinstance(detection_payload, dict):
        raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Worker returned an incomplete validation result.", stage="FINALIZATION", operation="deserialize_worker_result")

    entries: list[ArchiveEntry] = []
    raw_entries = detection_payload.get("archive_entries", [])
    if isinstance(raw_entries, list):
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            entries.append(
                ArchiveEntry(
                    name=str(raw.get("name", "")),
                    compressed_size=int(raw.get("compressed_size", 0)),
                    uncompressed_size=int(raw.get("uncompressed_size", 0)),
                    is_directory=bool(raw.get("is_directory", False)),
                    safe_path=bool(raw.get("safe_path", False)),
                    text_preview=None,
                    sha256=raw.get("sha256"),
                    detected_kind=raw.get("detected_kind"),
                    canonical_extension=raw.get("canonical_extension"),
                    mime_type=raw.get("mime_type"),
                    source_archive_sha256=raw.get("source_archive_sha256"),
                    depth=int(raw.get("depth", 0)),
                    security_findings=tuple(str(item) for item in raw.get("security_findings", []) if item),
                    processing_status=str(raw.get("processing_status", "inspected")),
                )
            )

    detection = DetectionResult(
        kind=str(detection_payload.get("kind", "unknown")),
        canonical_extension=detection_payload.get("canonical_extension"),
        mime_type=detection_payload.get("mime_type"),
        signature=str(detection_payload.get("signature", "")),
        archive_entries=tuple(entries),
        archive_kind=detection_payload.get("archive_kind"),
        archive_member_previews={},
        signature_match=bool(detection_payload.get("signature_match", True)),
        container_match=detection_payload.get("container_match"),
        validation_strength=str(detection_payload.get("validation_strength", "signature")),
        security_findings=tuple(str(item) for item in detection_payload.get("security_findings", []) if item),
        archive_depth=int(detection_payload.get("archive_depth", 0)),
        nested_archive_count=int(detection_payload.get("nested_archive_count", 0)),
    )
    validation = ValidationResult(
        accepted=bool(validation_payload.get("accepted", False)),
        reason=str(validation_payload.get("reason", "")),
        detection=detection,
        original_extension=str(validation_payload.get("original_extension", "")),
        effective_extension=validation_payload.get("effective_extension"),
        url_extension=validation_payload.get("url_extension"),
        url_match=validation_payload.get("url_match"),
        mime_type=validation_payload.get("mime_type"),
        declared_mime_type=validation_payload.get("declared_mime_type"),
        mime_match=validation_payload.get("mime_match"),
        content_disposition=validation_payload.get("content_disposition"),
        filename=validation_payload.get("filename"),
        file_sha256=validation_payload.get("file_sha256"),
        security_status=str(validation_payload.get("security_status", "unknown")),
        security_findings=tuple(str(item) for item in validation_payload.get("security_findings", []) if item),
        validation_strength=str(validation_payload.get("validation_strength", "signature")),
    )
    content_text = result.get("text")
    if not isinstance(content_text, str):
        raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Worker did not return extracted text.", stage="FINALIZATION", operation="deserialize_worker_result")
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    safe_artifacts: list[bytes] = []
    raw_artifacts = result.get("safe_image_artifacts_b64", [])
    if isinstance(raw_artifacts, list):
        for item in raw_artifacts:
            if not isinstance(item, str):
                continue
            try:
                safe_artifacts.append(base64.b64decode(item, validate=True))
            except Exception as exc:
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Worker returned an invalid sanitized image artifact.", details=str(exc), stage="FINALIZATION", operation="decode_safe_image") from exc
    return DownloadContent(text=content_text, validation=validation, metadata=dict(metadata), evidence=dict(evidence), safe_image_artifacts=tuple(safe_artifacts))


class DockerQuarantineRunner:
    """Run untrusted bytes inside a disposable, network-isolated container."""

    def __init__(self, project_root: Path, rules: dict[str, object]) -> None:
        self.project_root = Path(project_root)
        self.rules = rules
        self.image = str(rules.get("quarantine_docker_image", "scrapper-ollama-quarantine:2.0"))
        self.timeout = int(rules.get("quarantine_docker_timeout_seconds", 90))
        self.memory_mb = int(rules.get("quarantine_docker_memory_mb", 256))
        self.cpus = float(rules.get("quarantine_docker_cpus", 1.0))
        self.pids_limit = int(rules.get("quarantine_docker_pids_limit", 64))
        self.tmpfs_mb = int(rules.get("quarantine_docker_tmpfs_mb", 128))
        self.image_id: str | None = None
        self.runtime_security_options: list[str] = []
        self.rootless_detected = False
        self.rules_hash = _rules_hash(rules)

    def ensure_ready(self) -> None:
        """Verify Docker and the quarantine image before accepting untrusted data."""
        if not docker_available():
            raise DockerQuarantineError("QUARANTINE_DOCKER_UNAVAILABLE", "Docker is unavailable. Start Docker Desktop before processing downloaded files.", stage="DOCKER_INSPECTION", operation="verify_docker")
        if docker_image_exists(self.image):
            self.image_id = docker_image_id(self.image)
            if not self.image_id:
                raise DockerQuarantineError("QUARANTINE_IMAGE_ID_UNAVAILABLE", "The quarantine image exists but its immutable local image ID could not be read.", stage="DOCKER_INSPECTION", operation="inspect_image")
            expected = self.rules.get("quarantine_docker_expected_image_id")
            if expected and str(expected) != self.image_id:
                raise DockerQuarantineError("QUARANTINE_IMAGE_ID_MISMATCH", f"Configured quarantine image ID {expected!r} does not match the installed image ID {self.image_id!r}.", stage="DOCKER_INSPECTION", operation="verify_image_identity", status="REJECTED")
            self.runtime_security_options = _docker_runtime_security_options()
            self.rootless_detected = any("rootless" in item.lower() for item in self.runtime_security_options)
            return
        if bool(self.rules.get("quarantine_docker_auto_build", False)):
            print(f"[QUARANTINE] Building Docker image once: {self.image}", flush=True)
            build_quarantine_image(self.project_root, self.image)
            self.image_id = docker_image_id(self.image)
            if not self.image_id:
                raise DockerQuarantineError("QUARANTINE_IMAGE_ID_UNAVAILABLE", "Quarantine image build completed but its image ID could not be read.", stage="DOCKER_INSPECTION", operation="inspect_image")
            self.runtime_security_options = _docker_runtime_security_options()
            self.rootless_detected = any("rootless" in item.lower() for item in self.runtime_security_options)
            return
        raise DockerQuarantineError("QUARANTINE_IMAGE_MISSING", f"Docker image {self.image!r} is missing. Run scripts/build_quarantine_image.py once.", stage="DOCKER_INSPECTION", operation="inspect_image")

    def _container_command(self, *, container_name: str, source_name: str, metadata: dict[str, object] | None = None) -> list[str]:
        """Build the locked-down one-shot Docker command with sanitized download metadata only."""
        return [
            _docker_executable() or "docker",
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--user", "65532:65532",
            "--pids-limit", str(self.pids_limit),
            "--memory", f"{self.memory_mb}m",
            "--cpus", str(self.cpus),
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={self.tmpfs_mb}m",
            "-e", f"SCRAPPER_SOURCE_NAME={source_name}",
            "-e", f"SCRAPPER_RULES_JSON={_rules_for_container(self.rules)}",
            "-e", f"SCRAPPER_WORKER_SCHEMA_VERSION={self.rules.get('quarantine_worker_schema_version', '2.0')}",
            "-e", f"SCRAPPER_DOWNLOAD_METADATA={json.dumps(metadata or {}, ensure_ascii=False, separators=(',', ':'))}",
            "--name", container_name,
            self.image,
        ]

    def _run_container(
        self,
        command: list[str],
        payload: bytes,
        *,
        host_sha256: str,
        on_hash_verified: Callable[[], None] | None,
        job_id: str,
        container_name: str,
    ) -> dict[str, Any]:
        """Stream bytes in, verify the receipt hash, then consume only bounded JSON output."""
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={key: value for key, value in os.environ.copy().items() if key != "DOCKER_HOST"},
                bufsize=0,
            )
        except OSError as exc:
            raise DockerQuarantineError("QUARANTINE_DOCKER_EXEC_FAILED", f"Could not start the quarantine container: {exc}", details={"job_id": job_id}, stage="DOCKER_INSPECTION", operation="start_container") from exc

        lines: queue.Queue[bytes] = queue.Queue()

        def _read_stdout() -> None:
            assert process.stdout is not None
            for line in iter(process.stdout.readline, b""):
                lines.put(line)
            lines.put(b"")

        reader = threading.Thread(target=_read_stdout, name=f"quarantine-reader-{job_id}", daemon=True)
        reader.start()
        try:
            assert process.stdin is not None
            process.stdin.write(payload)
            process.stdin.close()
            try:
                first_line = lines.get(timeout=min(self.timeout, 15))
            except queue.Empty as exc:
                process.kill(); process.wait(timeout=5)
                raise DockerQuarantineError("QUARANTINE_TIMEOUT", "The quarantine worker did not acknowledge receipt in time.", details={"job_id": job_id}, stage="HASH", operation="verify_container_receipt", status="TIMEOUT") from exc
            if not first_line:
                stderr = process.stderr.read().decode("utf-8", "replace").strip() if process.stderr else ""
                process.wait(timeout=5)
                raise DockerQuarantineError("QUARANTINE_WORKER_FAILED", stderr or "Worker exited before receipt acknowledgement.", details={"job_id": job_id}, stage="DOCKER_INSPECTION", operation="worker_start")
            try:
                ack = json.loads(first_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                process.kill(); process.wait(timeout=5)
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Worker sent invalid receipt JSON.", details={"job_id": job_id}, stage="HASH", operation="parse_receipt") from exc
            if ack.get("phase") != "received":
                process.kill(); process.wait(timeout=5)
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Worker did not acknowledge the expected receipt phase.", details={"job_id": job_id}, stage="HASH", operation="verify_container_receipt")
            container_sha256 = str(ack.get("sha256", ""))
            if container_sha256 != host_sha256:
                process.kill(); process.wait(timeout=5)
                raise DockerQuarantineError("QUARANTINE_HASH_MISMATCH", "Container receipt SHA-256 does not match the host digest.", details={"job_id": job_id, "host_sha256": host_sha256, "container_sha256": container_sha256}, stage="HASH", operation="verify_container_receipt")
            if on_hash_verified is not None:
                try:
                    on_hash_verified()
                except Exception as exc:
                    process.kill(); process.wait(timeout=5)
                    raise DockerQuarantineError("CLEANUP_HOST_SOURCE_FAILED", f"Host source could not be removed after verified transfer: {exc}", details={"job_id": job_id, "host_sha256": host_sha256}, stage="QUARANTINE", operation="remove_transient_host_source") from exc
            try:
                process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill(); process.wait(timeout=5)
                raise DockerQuarantineError("QUARANTINE_TIMEOUT", f"Quarantine container exceeded the {self.timeout}s time limit.", details={"job_id": job_id}, stage="DOCKER_INSPECTION", operation="wait_container", status="TIMEOUT") from exc
            reader.join(timeout=2)
            remaining: list[bytes] = []
            while True:
                try:
                    line = lines.get_nowait()
                except queue.Empty:
                    break
                if line:
                    remaining.append(line)
            output = b"".join(remaining)
            max_output = int(self.rules.get("quarantine_max_output_kb", 4096)) * 1024
            if len(output) > max_output:
                raise DockerQuarantineError("QUARANTINE_OUTPUT_TOO_LARGE", "Quarantine worker returned more JSON output than allowed.", details={"job_id": job_id}, stage="FINALIZATION", operation="validate_worker_output")
            stderr = process.stderr.read().decode("utf-8", "replace").strip() if process.stderr else ""
            if process.returncode != 0:
                raise DockerQuarantineError("QUARANTINE_WORKER_FAILED", stderr or output.decode("utf-8", "replace").strip() or f"Container exited with code {process.returncode}.", details={"job_id": job_id, "returncode": process.returncode}, stage="FORMAT_PROCESSING", operation="worker_processing")
            lines_out = output.splitlines()
            if not lines_out:
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Quarantine worker returned no final JSON result.", details={"job_id": job_id}, stage="FINALIZATION", operation="parse_worker_result")
            try:
                result = json.loads(lines_out[-1].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Quarantine worker did not return valid final JSON.", details={"job_id": job_id}, stage="FINALIZATION", operation="parse_worker_result") from exc
            if not isinstance(result, dict):
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Worker returned a non-object JSON result.", stage="FINALIZATION", operation="validate_worker_schema")
            required = {"worker_schema_version", "status", "sha256", "validation", "metadata", "evidence", "stages"}
            missing = sorted(required - set(result))
            if missing:
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", f"Worker result is missing required fields: {', '.join(missing)}", details={"job_id": job_id}, stage="FINALIZATION", operation="validate_worker_schema")
            expected_schema = str(self.rules.get("quarantine_worker_schema_version", "2.0"))
            if str(result.get("worker_schema_version")) != expected_schema:
                raise DockerQuarantineError("QUARANTINE_SCHEMA_VERSION_MISMATCH", f"Worker schema {result.get('worker_schema_version')!r} does not match expected {expected_schema!r}.", stage="FINALIZATION", operation="validate_worker_schema", status="REJECTED")
            result_sha = str(result.get("sha256", ""))
            if len(result_sha) != 64 or any(ch not in "0123456789abcdef" for ch in result_sha.lower()):
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Worker returned an invalid SHA-256 value.", details={"job_id": job_id}, stage="FINALIZATION", operation="validate_worker_schema")
            if result_sha != host_sha256:
                raise DockerQuarantineError("QUARANTINE_HASH_MISMATCH", "Worker final-result SHA-256 does not match the verified input hash.", details={"job_id": job_id, "host_sha256": host_sha256, "container_sha256": result_sha}, stage="HASH", operation="verify_final_hash", status="REJECTED")
            if str(result.get("status")) not in {"accepted", "rejected", "unsupported", "inconclusive", "error"}:
                raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", "Worker returned an unsupported processing status.", details={"job_id": job_id}, stage="FINALIZATION", operation="validate_worker_schema")
            for key in ("validation", "metadata", "evidence", "stages"):
                if not isinstance(result.get(key), dict):
                    raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", f"Worker field {key!r} must be a JSON object.", details={"job_id": job_id}, stage="FINALIZATION", operation="validate_worker_schema")
            return result
        finally:
            reader.join(timeout=1)

    def process_bytes(
        self,
        payload: bytes,
        *,
        source_name: str,
        metadata: dict[str, object] | None = None,
        on_hash_verified: Callable[[], None] | None = None,
    ) -> DockerQuarantineResult:
        """Quarantine exact bytes, optionally scan them, then inspect them inside Docker."""
        if not payload:
            raise DockerQuarantineError("QUARANTINE_FILE_EMPTY", "Downloaded file is empty.", stage="QUARANTINE", operation="accept_artifact")
        max_bytes = int(self.rules.get("max_file_size_mb", 25)) * 1024 * 1024
        if len(payload) > max_bytes:
            raise DockerQuarantineError("QUARANTINE_FILE_TOO_LARGE", f"Download exceeds the configured {max_bytes} byte limit.", stage="QUARANTINE", operation="accept_artifact")

        host_sha256 = sha256_bytes(payload)
        quarantine_path, sidecar = _persist_quarantine_artifact(
            self.project_root,
            payload,
            host_sha256,
            source_name=source_name,
            metadata=metadata,
        )

        malware = run_antimalware_scan(quarantine_path, self.rules)
        if malware.status == "malware":
            sidecar.write_text(json.dumps({"schema_version": "1.0", "artifact_id": host_sha256, "sha256": host_sha256, "size": len(payload), "retention_state": "rejected", "antimalware": malware.__dict__}, ensure_ascii=False, indent=2), encoding="utf-8")
            raise DockerQuarantineError("ANTIMALWARE_MALWARE_DETECTED", malware.message, details=malware.__dict__, stage="SECURITY_VALIDATION", operation="antimalware_scan", status="REJECTED")
        if malware.status in {"unavailable", "error"} and str(self.rules.get("antimalware", {}).get("mode", "auto")).lower() == "required":
            raise DockerQuarantineError("ANTIMALWARE_SCAN_FAILED", malware.message, details=malware.__dict__, stage="SECURITY_VALIDATION", operation="antimalware_scan")

        self.ensure_ready()
        job_id = uuid.uuid4().hex
        container_name = f"scrapper-quarantine-{job_id}"
        source_name = Path(source_name).name or "download"
        command = self._container_command(container_name=container_name, source_name=source_name, metadata=metadata)
        try:
            result = self._run_container(
                command,
                payload,
                host_sha256=host_sha256,
                on_hash_verified=on_hash_verified,
                job_id=job_id,
                container_name=container_name,
            )
        except DockerQuarantineError:
            # Keep raw artifact and provenance on every rejected/error path.
            raise

        status = str(result.get("status", "")).lower()
        if status == "rejected":
            raise DockerQuarantineError(
                str(result.get("code", "SECURITY_FILE_REJECTED")),
                str(result.get("reason", "The file was rejected by the quarantine worker.")),
                details={"job_id": job_id, "quarantine_path": str(quarantine_path), "quarantine_sidecar": str(sidecar), "validation": result.get("validation"), "stages": result.get("stages")},
                stage=(result.get("stages") or {}).get("first_failure", {}).get("pipeline_stage", "SECURITY_VALIDATION") if isinstance(result.get("stages"), dict) else "SECURITY_VALIDATION",
                operation=(result.get("stages") or {}).get("first_failure", {}).get("operation", "worker_validation") if isinstance(result.get("stages"), dict) else "worker_validation",
                status="REJECTED",
            )
        if status in {"unsupported", "inconclusive"}:
            raise DockerQuarantineError(
                str(result.get("code", "FORMAT_UNSUPPORTED")),
                str(result.get("reason", f"Worker returned {status}.")),
                details={"job_id": job_id, "quarantine_path": str(quarantine_path), "quarantine_sidecar": str(sidecar), "stages": result.get("stages")},
                stage=(result.get("stages") or {}).get("first_failure", {}).get("pipeline_stage", "FORMAT_PROCESSING") if isinstance(result.get("stages"), dict) else "FORMAT_PROCESSING",
                operation=(result.get("stages") or {}).get("first_failure", {}).get("operation", "worker_processing") if isinstance(result.get("stages"), dict) else "worker_processing",
                status=status.upper(),
            )
        if str(result.get("sha256", "")) != host_sha256:
            raise DockerQuarantineError(
                "QUARANTINE_HASH_MISMATCH",
                "Worker final-result SHA-256 does not match the host digest.",
                details={"job_id": job_id, "host_sha256": host_sha256, "container_sha256": result.get("sha256")},
                stage="HASH",
                operation="verify_final_hash",
                status="REJECTED",
            )
        if status != "accepted":
            raise DockerQuarantineError("QUARANTINE_INVALID_RESULT", f"Worker returned unsupported status {status!r}.", details={"job_id": job_id, "stages": result.get("stages")}, stage="FINALIZATION", operation="validate_worker_status")

        content = _deserialize_content(result)
        content.metadata.update(
            {
                "quarantine_backend": "docker",
                "quarantine_job_id": job_id,
                "quarantine_image": self.image,
                "quarantine_image_id": self.image_id,
                "quarantine_path": str(quarantine_path),
                "quarantine_sidecar": str(sidecar),
                "host_sha256": host_sha256,
                "container_sha256": str(result.get("sha256", "")),
                "integrity_verified": True,
                "source_size": len(payload),
                "antimalware": malware.__dict__,
                "stage_report": result.get("stages"),
                "worker_schema_version": result.get("worker_schema_version"),
                "worker_config_hash": result.get("worker_config_hash"),
                "docker_runtime_security_options": list(self.runtime_security_options),
                "docker_rootless_detected": self.rootless_detected,
                "docker_rootless_preferred": bool(self.rules.get("quarantine_prefer_rootless", True)),
                "host_source_deleted": False,
            }
        )
        sidecar.write_text(json.dumps({"schema_version": "1.0", "artifact_id": host_sha256, "sha256": host_sha256, "size": len(payload), "retention_state": "accepted_pending_cleanup", "metadata": metadata or {}, "antimalware": malware.__dict__, "worker_stages": result.get("stages"), "worker_schema_version": result.get("worker_schema_version"), "worker_config_hash": result.get("worker_config_hash"), "docker_runtime_security_options": list(self.runtime_security_options), "docker_rootless_detected": self.rootless_detected, "docker_rootless_preferred": bool(self.rules.get("quarantine_prefer_rootless", True)), "image_id": self.image_id, "recorded_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
        return DockerQuarantineResult(
            job_id=job_id,
            image=self.image,
            image_digest=self.image_id,
            host_sha256=host_sha256,
            container_sha256=str(result.get("sha256", "")),
            source_size=len(payload),
            quarantine_path=quarantine_path,
            content=content,
        )
