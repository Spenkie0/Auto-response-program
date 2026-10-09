from __future__ import annotations

import json
from pathlib import Path

from ..config import project_path

DEFAULT_RULES: dict[str, object] = {
    "accepted_extensions": [],
    "extension_aliases": {},
    "max_file_size_mb": 25,
    "max_archive_size_mb": 25,
    "max_archive_uncompressed_size_mb": 100,
    "max_total_extracted_size_mb": 150,
    "max_member_size_mb": 50,
    "max_archive_member_size_mb": 50,
    "max_archive_entries": 1000,
    "max_member_count": 1000,
    "max_total_member_count": 3000,
    "max_compression_ratio": 100,
    "max_archive_depth": 3,
    "max_nested_archives": 8,
    "max_processing_time_seconds": 90,
    "max_text_chars": 400000,
    "max_xml_bytes": 10000000,
    "max_relationship_count": 10000,
    "max_embedded_images": 32,
    "allow_external_relationships": False,
    "max_visual_images": 8,
    "max_image_pixels": 40000000,
    "max_image_dimension": 20000,
    "max_safe_image_output_kb": 512,
    "max_ocr_text_chars": 400000,
    "ocr_enabled": True,
    "tesseract_executable": "tesseract",
    "ocr_language": "fra+eng",
    "ocr_psm": 6,
    "ocr_timeout_seconds": 30,
    "download_image_visual_analysis": True,
    "download_timeout_seconds": 30,
    "download_connect_timeout_seconds": 10,
    "download_read_timeout_seconds": 30,
    "download_same_origin_only": True,
    "download_allowed_hosts": [],
    "download_allowed_ip_ranges": [],
    "download_allow_private_ips": False,
    "download_max_redirects": 5,
    "download_max_header_bytes": 65536,
    "download_max_response_bytes": 26214400,
    "download_user_agent": "Scrapper-Ollama/2.0",
    "download_max_url_length": 4096,
    "download_allowed_schemes": ["http", "https"],
    "download_dns_pin": True,
    "quarantine_backend": "docker",
    "quarantine_docker_image": "scrapper-ollama-quarantine:2.0",
    "quarantine_docker_memory_mb": 256,
    "quarantine_docker_cpus": 1.0,
    "quarantine_docker_pids_limit": 64,
    "quarantine_docker_tmpfs_mb": 128,
    "quarantine_docker_timeout_seconds": 90,
    "quarantine_docker_auto_build": False,
    "quarantine_worker_schema_version": "2.0",
    "quarantine_docker_expected_image_id": None,
    "quarantine_prefer_rootless": True,
    "quarantine_max_output_kb": 4096,
    "quarantine_retention_days": 30,
    "quarantine_safe_artifact_max_kb": 512,
    "delete_download_after_high_confidence": True,
    "download_delete_confidence_threshold": 0.9,
    "antimalware": {"mode": "auto", "timeout_seconds": 120},
}


def load_download_rules(path: str | None = None) -> dict[str, object]:
    """Load download-security rules from JSON and normalize configurable collections."""
    rules_path = Path(path) if path else project_path("config", "download_rules.json")
    if not rules_path.exists():
        return json.loads(json.dumps(DEFAULT_RULES))
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULT_RULES))
    if isinstance(data, dict):
        merged.update(data)

    merged["accepted_extensions"] = [
        str(value).lower() if str(value).startswith(".") else f".{str(value).lower()}"
        for value in merged.get("accepted_extensions", [])
    ]
    aliases: dict[str, list[str]] = {}
    raw_aliases = merged.get("extension_aliases", {})
    if isinstance(raw_aliases, dict):
        for key, values in raw_aliases.items():
            canonical = str(key).lower()
            if not canonical.startswith("."):
                canonical = f".{canonical}"
            normalized: list[str] = []
            if isinstance(values, (list, tuple, set)):
                for value in values:
                    ext = str(value).lower()
                    if not ext.startswith("."):
                        ext = f".{ext}"
                    normalized.append(ext)
            aliases[canonical] = sorted(set(normalized))
    merged["extension_aliases"] = aliases
    merged["download_allowed_hosts"] = [
        str(value).strip().lower().rstrip(".")
        for value in merged.get("download_allowed_hosts", [])
        if str(value).strip()
    ]
    merged["download_allowed_ip_ranges"] = [
        str(value).strip()
        for value in merged.get("download_allowed_ip_ranges", [])
        if str(value).strip()
    ]
    antimalware = merged.get("antimalware")
    if not isinstance(antimalware, dict):
        merged["antimalware"] = {"mode": "auto", "timeout_seconds": 120}
    return merged
