from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from app.downloads.docker_quarantine import DockerQuarantineError, DockerQuarantineResult
from app.downloads.extract import extract_safe_text
from app.downloads.models import DownloadIngestionError
from app.downloads.security import validate_download


class InProcessQuarantineRunner:
    """Test double for DockerQuarantineRunner; production never uses host-side parsing."""

    def __init__(self, project_root: Path, rules: dict[str, object]) -> None:
        self.project_root = Path(project_root)
        self.rules = rules
        self.image = "test-quarantine@sha256:test"

    def process_bytes(
        self,
        payload: bytes,
        *,
        source_name: str,
        metadata: dict[str, object] | None = None,
        on_hash_verified: Callable[[], None] | None = None,
    ) -> DockerQuarantineResult:
        digest = hashlib.sha256(payload).hexdigest()
        quarantine_dir = self.project_root / "data" / "downloads" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        quarantine_path = quarantine_dir / f"{digest}.bin"
        quarantine_path.write_bytes(payload)
        sidecar = quarantine_dir / f"{digest}.json"
        sidecar.write_text(json.dumps(metadata or {}, ensure_ascii=False), encoding="utf-8")
        if on_hash_verified:
            on_hash_verified()
        try:
            worker_metadata = metadata or {}
            validation = validate_download(
                quarantine_path,
                self.rules,
                source_url=str(worker_metadata.get("source_url") or "") or None,
                content_type=str(worker_metadata.get("content_type") or "") or None,
                content_disposition=str(worker_metadata.get("content_disposition") or "") or None,
                url_extension=worker_metadata.get("url_extension"),
                filename=str(worker_metadata.get("filename") or source_name),
                file_sha256=digest,
            )
            if not validation.accepted:
                raise DockerQuarantineError(
                    validation.reason,
                    validation.reason,
                    details={"job_id": "test-job", "quarantine_path": str(quarantine_path)},
                    stage="SECURITY_VALIDATION",
                    operation="validate_security_policy",
                    status="REJECTED",
                )
            content = extract_safe_text(
                quarantine_path,
                validation,
                int(self.rules.get("max_text_chars", 400000)),
                rules=self.rules,
            )
            content.metadata.update(
                {
                    "quarantine_backend": "docker-test-double",
                    "quarantine_job_id": "test-job",
                    "quarantine_path": str(quarantine_path),
                    "quarantine_sidecar": str(sidecar),
                    "quarantine_image": self.image,
                    "quarantine_image_id": self.image,
                    "host_sha256": digest,
                    "container_sha256": digest,
                    "integrity_verified": True,
                    "source_size": len(payload),
                    "antimalware": {"status": "test-skipped"},
                    "stage_report": {"failed": False, "stages": []},
                    "host_source_deleted": False,
                }
            )
            return DockerQuarantineResult(
                job_id="test-job",
                image=self.image,
                image_digest=self.image,
                host_sha256=digest,
                container_sha256=digest,
                source_size=len(payload),
                quarantine_path=quarantine_path,
                content=content,
            )
        except DownloadIngestionError:
            raise
