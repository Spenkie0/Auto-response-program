from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.downloads.docker_quarantine import DockerQuarantineRunner
from app.downloads.rules import load_download_rules
from app.downloads.workflow import process_download


class DockerQuarantineRunnerTests(unittest.TestCase):
    @staticmethod
    def _worker_payload(data: bytes, *, status: str = "accepted", sha256: str | None = None) -> bytes:
        digest = sha256 or hashlib.sha256(data).hexdigest()
        return json.dumps(
            {
                "status": status,
                "sha256": digest,
                "text": "hello from the isolated worker" if status == "accepted" else None,
                "metadata": {"source_name": "document.txt", "worker_security_boundary": "docker"},
                "validation": {
                    "accepted": True,
                    "reason": "accepted",
                    "original_extension": ".txt",
                    "effective_extension": ".txt",
                    "detection": {
                        "kind": "text",
                        "canonical_extension": ".txt",
                        "mime_type": "text/plain",
                        "signature": "61 62",
                        "archive_kind": None,
                        "archive_entries": [],
                    },
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def test_worker_emits_receipt_and_final_result(self) -> None:
        payload = b"hello worker"
        rules = {
            "accepted_extensions": [".txt"],
            "max_file_size_mb": 25,
            "max_archive_entries": 1000,
            "max_uncompressed_size_mb": 100,
            "max_archive_member_size_mb": 50,
            "max_compression_ratio": 100,
            "max_text_chars": 1000,
        }
        env = os.environ.copy()
        env["SCRAPPER_SOURCE_NAME"] = "worker.txt"
        env["SCRAPPER_RULES_JSON"] = json.dumps(rules)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "quarantine" / "worker_entry.py")],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        lines = [line for line in completed.stdout.decode("utf-8").splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 2)
        receipt = json.loads(lines[0])
        result = json.loads(lines[-1])
        self.assertEqual(receipt["phase"], "received")
        self.assertEqual(receipt["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["sha256"], receipt["sha256"])
        self.assertEqual(result["text"], "hello worker")

    def test_container_command_has_strong_runtime_restrictions(self) -> None:
        payload = b"hello"
        rules = {**load_download_rules(), "quarantine_docker_image": "test-quarantine:1"}
        runner = DockerQuarantineRunner(Path("."), rules)
        with patch("app.downloads.docker_quarantine.docker_available", return_value=True), \
             patch("app.downloads.docker_quarantine.docker_image_exists", return_value=True), \
             patch("app.downloads.docker_quarantine.docker_image_id", return_value="sha256:test-image"), \
             patch("app.downloads.docker_quarantine._docker_runtime_security_options", return_value=["name=seccomp", "name=rootless"]), \
             patch.object(runner, "_run_container") as run_container:
            run_container.return_value = json.loads(self._worker_payload(payload))
            result = runner.process_bytes(payload, source_name="document.txt")

        command = run_container.call_args.args[0]
        self.assertIn("--rm", command)
        self.assertIn("--pull=never", command)
        self.assertIn("--network", command)
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("ALL", command)
        self.assertIn("--security-opt", command)
        self.assertIn("no-new-privileges:true", command)
        self.assertIn("--user", command)
        self.assertNotIn("-v", command)
        self.assertNotIn("--volume", command)
        self.assertIn("--pids-limit", command)
        self.assertIn("--memory", command)
        self.assertIn("--cpus", command)
        self.assertIn("--tmpfs", command)
        self.assertTrue(result.content.metadata["integrity_verified"])
        self.assertTrue(result.content.metadata["docker_rootless_detected"])
        self.assertEqual(result.host_sha256, result.container_sha256)

    def test_docker_backend_does_not_fall_back_to_host_parsing(self) -> None:
        from app.downloads.docker_quarantine import DockerQuarantineError
        rules = {**load_download_rules(), "quarantine_docker_auto_build": False}
        runner = DockerQuarantineRunner(Path("."), rules)
        with patch("app.downloads.docker_quarantine.docker_available", return_value=False):
            with self.assertRaises(DockerQuarantineError) as ctx:
                runner.ensure_ready()
        self.assertEqual(ctx.exception.code, "QUARANTINE_DOCKER_UNAVAILABLE")

    def test_hash_mismatch_is_rejected(self) -> None:
        payload = b"hello"
        rules = {**load_download_rules(), "quarantine_docker_image": "test-quarantine:1"}
        runner = DockerQuarantineRunner(Path("."), rules)
        with patch("app.downloads.docker_quarantine.docker_available", return_value=True), \
             patch("app.downloads.docker_quarantine.docker_image_exists", return_value=True), \
             patch("app.downloads.docker_quarantine.docker_image_id", return_value="sha256:test-image"), \
             patch("app.downloads.docker_quarantine._docker_runtime_security_options", return_value=["name=seccomp", "name=rootless"]), \
             patch.object(runner, "_run_container") as run_container:
            run_container.return_value = json.loads(self._worker_payload(payload, sha256="0" * 64))

            with self.assertRaises(Exception) as ctx:
                runner.process_bytes(payload, source_name="document.txt")

        self.assertEqual(ctx.exception.code, "QUARANTINE_HASH_MISMATCH")


class DockerWorkflowHandoffTests(unittest.TestCase):
    def test_transient_host_source_is_deleted_after_verified_handoff(self) -> None:
        payload = b"hello world"
        rules = {**load_download_rules(), "quarantine_backend": "docker"}
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "incoming.download"
            source.write_bytes(payload)

            from app.downloads.models import DetectionResult, DownloadContent, ValidationResult

            validation = ValidationResult(
                accepted=True,
                reason="accepted",
                detection=DetectionResult("text", ".txt", "text/plain", "68 65"),
                original_extension=".download",
                effective_extension=".txt",
            )
            fake_content = DownloadContent(
                text="hello world",
                validation=validation,
                metadata={"source_name": "incoming.download", "quarantine_backend": "docker", "quarantine_job_id": "job1"},
            )

            class FakeRunner:
                def __init__(self, project_root, rules):
                    pass

                def process_bytes(self, data, *, source_name, metadata=None, on_hash_verified=None):
                    from app.downloads.docker_quarantine import DockerQuarantineResult
                    self_seen = hashlib.sha256(data).hexdigest()
                    if on_hash_verified is not None:
                        on_hash_verified()
                    return DockerQuarantineResult(
                        job_id="job1",
                        image="test-quarantine:1",
                        image_digest="sha256:test-image",
                        host_sha256=self_seen,
                        container_sha256=self_seen,
                        source_size=len(data),
                        quarantine_path=base / "data" / "downloads" / "quarantine" / f"{self_seen}.bin",
                        content=fake_content,
                    )

            with patch("app.downloads.workflow.DockerQuarantineRunner", FakeRunner):
                result = process_download(
                    source,
                    base,
                    rules=rules,
                    delete_source_after_handoff=True,
                )

            self.assertFalse(source.exists())
            self.assertTrue(result.content.metadata["host_source_deleted"])
            self.assertEqual(result.content.metadata["host_source_sha256"], hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
