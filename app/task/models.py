from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskMode(str, Enum):
    """Top-level execution mode for one captured page."""

    BASIC = "basic"
    FILE_TASK = "file_task"
    SANDBOX = "sandbox"


class TaskResponseMode(str, Enum):
    """How a non-sandbox task appears to request its final answer."""

    CHOICE = "choice"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SandboxFrame:
    """Describe an iframe that is likely to contain the interactive sandbox."""

    src: str
    title: str
    classes: tuple[str, ...]
    sandbox_score: int
    reason_codes: tuple[str, ...]
    same_origin: bool | None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation."""
        return {
            "src": self.src,
            "title": self.title,
            "classes": list(self.classes),
            "sandbox_score": self.sandbox_score,
            "reason_codes": list(self.reason_codes),
            "same_origin": self.same_origin,
        }


@dataclass(frozen=True)
class TaskClassification:
    """Deterministic classification of the current clean page."""

    mode: TaskMode
    response_mode: TaskResponseMode
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    sandbox_frames: tuple[SandboxFrame, ...] = field(default_factory=tuple)
    download_candidate_count: int = 0
    download_candidate_urls: tuple[str, ...] = field(default_factory=tuple)
    ambiguous_download: bool = False

    @property
    def has_sandbox(self) -> bool:
        """Return whether a sandbox frame was identified."""
        return bool(self.sandbox_frames)

    @property
    def has_download_signal(self) -> bool:
        """Return whether the page contains a download/file-task signal."""
        return self.download_candidate_count > 0 or self.mode == TaskMode.FILE_TASK

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON-friendly representation."""
        return {
            "mode": self.mode.value,
            "response_mode": self.response_mode.value,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "sandbox_frames": [frame.as_dict() for frame in self.sandbox_frames],
            "download_candidate_count": self.download_candidate_count,
            "download_candidate_urls": list(self.download_candidate_urls),
            "ambiguous_download": self.ambiguous_download,
        }
