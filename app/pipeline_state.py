from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Any


STAGES = (
    "CAPTURE",
    "TASK_DETECTION",
    "DOWNLOAD_DISCOVERY",
    "DOWNLOAD",
    "QUARANTINE",
    "HASH",
    "METADATA",
    "TYPE_DETECTION",
    "DOCKER_INSPECTION",
    "SECURITY_VALIDATION",
    "TYPE_CONSISTENCY",
    "FORMAT_PROCESSING",
    "IMAGE_OCR_VISION",
    "DOCUMENT_TEXT",
    "EVIDENCE_ASSEMBLY",
    "TASK_SOLVING",
    "ANSWER_VALIDATION",
    "FINAL_ANSWER",
    "CLEANUP",
)

TERMINAL_STATUSES = {"SUCCESS", "REJECTED", "UNSUPPORTED", "INCONCLUSIVE", "FAILED", "TIMEOUT", "CANCELLED"}


@dataclass
class PipelineRunState:
    """Enforce the top-level pipeline contract and preserve first meaningful failure."""

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    current_stage: str | None = None
    _last_stage_index: int = -1
    records: list[dict[str, Any]] = field(default_factory=list)
    first_failure: dict[str, Any] | None = None
    file_sha256: str | None = None

    def enter(self, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        stage_index = STAGES.index(stage)
        if stage_index < self._last_stage_index:
            raise RuntimeError(
                f"Illegal pipeline transition: {STAGES[self._last_stage_index]} -> {stage}"
            )
        self._last_stage_index = stage_index
        self.current_stage = stage
        self.records.append({
            "pipeline_stage": stage,
            "status": "RUNNING",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def finish(
        self,
        stage: str,
        status: str = "SUCCESS",
        *,
        operation: str | None = None,
        output: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        status = status.upper()
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"Invalid pipeline status: {status}")
        for record in reversed(self.records):
            if record["pipeline_stage"] == stage and record["status"] == "RUNNING":
                record.update({
                    "status": status,
                    "operation": operation or stage,
                    "output": output or {},
                    "error_code": error_code,
                    "error_message": error_message,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                break
        else:
            self.records.append({
                "pipeline_stage": stage,
                "status": status,
                "operation": operation or stage,
                "output": output or {},
                "error_code": error_code,
                "error_message": error_message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        if status != "SUCCESS" and self.first_failure is None:
            for candidate in reversed(self.records):
                if candidate.get("pipeline_stage") == stage and candidate.get("status") == status:
                    self.first_failure = dict(candidate)
                    break
            if self.first_failure is None:
                self.first_failure = dict(self.records[-1])

    def fail(self, stage: str, code: str, message: str, *, operation: str | None = None, status: str = "FAILED", output: dict[str, Any] | None = None) -> None:
        self.finish(stage, status, operation=operation, output=output, error_code=code, error_message=message)

    def set_hash(self, sha256: str | None) -> None:
        if sha256:
            self.file_sha256 = sha256

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "request_id": self.request_id,
            "started_at": self.started_at,
            "current_stage": self.current_stage,
            "file_sha256": self.file_sha256,
            "first_failure": self.first_failure,
            "records": list(self.records),
            "transition_index": self._last_stage_index,
        }
