from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import time
from typing import Any


class PipelineStage(str, Enum):
    """Observable file-pipeline stages used by the quarantine worker and host handoff."""

    QUARANTINE = "QUARANTINE"
    HASH = "HASH"
    METADATA = "METADATA"
    TYPE_DETECTION = "TYPE_DETECTION"
    DOCKER_INSPECTION = "DOCKER_INSPECTION"
    SECURITY_VALIDATION = "SECURITY_VALIDATION"
    TYPE_CONSISTENCY = "TYPE_CONSISTENCY"
    FORMAT_PROCESSING = "FORMAT_PROCESSING"
    DOCUMENT_TEXT = "DOCUMENT_TEXT"
    IMAGE_PROCESSING = "IMAGE_PROCESSING"
    OCR = "OCR"
    VISION = "VISION"
    EVIDENCE_ASSEMBLY = "EVIDENCE_ASSEMBLY"
    FINALIZATION = "FINALIZATION"
    CLEANUP = "CLEANUP"


class StageStatus(str, Enum):
    """Terminal stage states."""

    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    UNSUPPORTED = "UNSUPPORTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


@dataclass
class StageRecord:
    """Machine-readable record for one pipeline stage."""

    pipeline_stage: str
    operation: str
    status: str
    timestamp: float
    duration_ms: float = 0.0
    error_code: str | None = None
    error_message: str | None = None
    exception_type: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    previous_stage: str | None = None
    next_stage: str | None = None
    file_sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return asdict(self)


class StageMachine:
    """Enforce monotonic stage progression and preserve the first meaningful failure."""

    def __init__(self, ordered_stages: list[PipelineStage]) -> None:
        self.ordered_stages = list(ordered_stages)
        self.records: list[StageRecord] = []
        self.failed = False
        self.first_failure: StageRecord | None = None

    def run(
        self,
        stage: PipelineStage,
        operation: str,
        func,
        *,
        input_data: dict[str, Any] | None = None,
        file_sha256: str | None = None,
    ) -> Any:
        """Run one stage, record its outcome, and stop future stages after failure."""
        if self.failed:
            raise RuntimeError("STAGE_MACHINE_STOPPED")
        if stage not in self.ordered_stages:
            raise ValueError(f"Stage is not part of this pipeline: {stage}")
        now = time.perf_counter()
        timestamp = time.time()
        previous = self.records[-1].pipeline_stage if self.records else None
        index = self.ordered_stages.index(stage)
        next_stage = self.ordered_stages[index + 1].value if index + 1 < len(self.ordered_stages) else None
        try:
            result = func()
        except Exception as exc:
            record = StageRecord(
                pipeline_stage=stage.value,
                operation=operation,
                status=StageStatus.FAILED.value,
                timestamp=timestamp,
                duration_ms=(time.perf_counter() - now) * 1000.0,
                error_code=getattr(exc, "code", "UNEXPECTED"),
                error_message=str(exc),
                exception_type=type(exc).__name__,
                input=dict(input_data or {}),
                previous_stage=previous,
                next_stage=next_stage,
                file_sha256=file_sha256,
            )
            self.records.append(record)
            self.failed = True
            self.first_failure = record
            raise
        output = result if isinstance(result, dict) else {}
        record = StageRecord(
            pipeline_stage=stage.value,
            operation=operation,
            status=StageStatus.SUCCESS.value,
            timestamp=timestamp,
            duration_ms=(time.perf_counter() - now) * 1000.0,
            input=dict(input_data or {}),
            output=dict(output),
            previous_stage=previous,
            next_stage=next_stage,
            file_sha256=file_sha256,
        )
        self.records.append(record)
        return result

    def reject(
        self,
        stage: PipelineStage,
        operation: str,
        code: str,
        message: str,
        *,
        input_data: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        file_sha256: str | None = None,
        status: StageStatus = StageStatus.REJECTED,
    ) -> None:
        """Record an expected terminal security/policy outcome."""
        previous = self.records[-1].pipeline_stage if self.records else None
        index = self.ordered_stages.index(stage)
        next_stage = self.ordered_stages[index + 1].value if index + 1 < len(self.ordered_stages) else None
        record = StageRecord(
            pipeline_stage=stage.value,
            operation=operation,
            status=status.value,
            timestamp=time.time(),
            error_code=code,
            error_message=message,
            input=dict(input_data or {}),
            output=dict(output or {}),
            previous_stage=previous,
            next_stage=next_stage,
            file_sha256=file_sha256,
        )
        self.records.append(record)
        self.failed = True
        self.first_failure = record

    def as_dict(self) -> dict[str, Any]:
        """Return the complete machine-readable state."""
        return {
            "failed": self.failed,
            "first_failure": self.first_failure.as_dict() if self.first_failure else None,
            "stages": [record.as_dict() for record in self.records],
        }
