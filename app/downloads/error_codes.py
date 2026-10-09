from __future__ import annotations

ERROR_CODE_PREFIXES = {
    "DOWNLOAD_": "download and network transfer",
    "QUARANTINE_": "quarantine and Docker handoff",
    "HASH_": "hashing and provenance",
    "METADATA_": "download metadata",
    "TYPE_": "byte/type detection",
    "ARCHIVE_": "archive and package security",
    "SECURITY_": "security policy",
    "ANTIMALWARE_": "Windows Defender / antimalware",
    "IMAGE_": "image validation and processing",
    "OCR_": "OCR",
    "DOCUMENT_": "document/container processing",
    "VISION_": "vision analysis",
    "TASK_": "task processing",
    "MODEL_": "model interaction",
    "ANSWER_": "answer normalization/validation",
    "CLEANUP_": "cleanup",
    "DEPENDENCY_": "dependency diagnostics",
}


def is_known_error_code(code: str) -> bool:
    """Return whether a machine-readable error code belongs to a known family."""
    return any(str(code).startswith(prefix) for prefix in ERROR_CODE_PREFIXES)
