from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

from .config import (
    ANSWER_FOLDER,
    ANSWER_NOT_FOUND_FOLDER,
    DEFAULT_COMPACT_ANSWER_CHARS,
    DEFAULT_MAX_ANSWER_CHARS,
    DEFAULT_MAX_CANDIDATE_CHARS,
)
from .answer_representation import build_compact_representation, summarize_attachment_metadata
from .answering.deterministic import solve_deterministic_task
from .answering.choice_resolver import resolve_choice_answer
from .downloads.hints import page_may_require_download
from .downloads.hints import find_download_candidates, page_may_require_download, select_download_candidate
from .downloads.workflow import (
    DownloadWorkflowError,
    delete_quarantined_download,
    download_and_process,
    process_download,
    save_processing_metadata,
)
from .downloads.models import DownloadIngestionError
from .downloads.rules import load_download_rules
from .debug.profiling import normalize_ollama_metrics, print_metrics, save_profile
from .errors.handler import error_payload, format_error, save_error_report
from .ollama.client import (
    InvalidOllamaResponseError,
    ModelNotInstalledError,
    OllamaClient,
    OllamaError,
)
from .ollama.prompts import answerer_prompt, window_verifier_prompt
from .parsing.source import extract_html
from .task import classify_task
from .storage.writer import save_answer_json, save_clean_page, save_review
from .window.extractor import Candidate, ExtractionResult, WindowExtractor
from .vision import analyze_screenshot, print_screenshot_cli
from .dependency_installer import find_tesseract, ensure_app, load_dependency_config, ensure_tesseract_languages
from .cli_view import print_answer_result, print_capture_header, print_final_answer, print_task_details
from .pipeline_state import PipelineRunState


class Pipeline:
    def __init__(
        self,
        base_dir: Path,
        extractor: WindowExtractor,
        ollama: OllamaClient | None,
        window_model: str,
        answer_model: str,
        vision_model: str = "",
        use_screenshot_with_vision: bool = True,
        ocr_enabled: bool = True,
        ocr_executable: str = "tesseract",
        ocr_language: str = "fra+eng",
        visual_analysis_enabled: bool = True,
        use_window_llm: bool = True,
        use_answer_llm: bool = True,
        max_answer_chars: int = DEFAULT_MAX_ANSWER_CHARS,
        answer_input_mode: str = "compact",
        download_rules: dict[str, object] | None = None,
        compact_answer_chars: int = DEFAULT_COMPACT_ANSWER_CHARS,
        overwrite: bool = False,
        dry_run: bool = False,
        missing_model_handler: Callable[[str, str, str | None], bool] | None = None,
    ) -> None:
        """Initialize the extraction, Ollama, storage, and error-handling pipeline."""
        self.base_dir = Path(base_dir)
        self.extractor = extractor
        self.ollama = ollama
        self.window_model = window_model
        self.answer_model = answer_model
        self.vision_model = vision_model.strip()
        self.use_screenshot_with_vision = use_screenshot_with_vision
        self.ocr_enabled = ocr_enabled
        self.ocr_executable = ocr_executable
        self.ocr_language = ocr_language
        self.visual_analysis_enabled = visual_analysis_enabled
        self.use_window_llm = use_window_llm
        self.use_answer_llm = use_answer_llm
        self.max_answer_chars = max_answer_chars
        self.answer_input_mode = answer_input_mode
        self.download_rules = download_rules or load_download_rules()
        self.compact_answer_chars = compact_answer_chars
        self.overwrite = overwrite
        self.dry_run = dry_run
        self.missing_model_handler = missing_model_handler
        self._active_run_state: PipelineRunState | None = None

    def process_raw_file(
        self,
        raw_path: Path,
        page_url: str | None = None,
        title: str | None = None,
        attachment_path: Path | None = None,
        screenshot_path: Path | None = None,
    ) -> dict[str, Any]:
        """Process one raw capture through extraction, optional attachment ingestion, answering, and storage."""
        self._active_run_state = PipelineRunState()
        self._active_run_state.enter("CAPTURE")
        try:
            raw_html = raw_path.read_text(encoding="utf-8", errors="replace")
            self._active_run_state.finish("CAPTURE", output={"html_characters": len(raw_html), "filename": raw_path.name})
        except OSError as exc:
            self._active_run_state.fail("CAPTURE", "FILE_IO", f"Could not read raw HTML: {exc}", operation="read_raw")
            return self._error(raw_path, "read_raw", "FILE_IO", f"Could not read raw HTML: {exc}")

        print_capture_header(
            raw_name=raw_path.name,
            url=page_url,
            title=title,
            html_chars=len(raw_html),
            screenshot_path=str(screenshot_path) if screenshot_path else None,
        )

        try:
            clean_source, _ = extract_html(raw_html, "strip")
            extraction = self.extractor.extract(clean_source)
        except Exception as exc:
            return self._error(raw_path, "window_extraction", "WINDOW_NOT_FOUND", f"HTML extraction failed: {exc}")

        if extraction.status == "accepted":
            clean_html = extraction.html or ""
            extraction_route = "deterministic"
        else:
            if not self.use_window_llm or not self.ollama:
                return self._review(raw_path, extraction, "window LLM disabled or unavailable")
            verified = self._verify_candidates(extraction.candidates, raw_path.name, raw_path)
            if verified.get("status") == "error":
                return self._error(
                    raw_path,
                    "window_verifier",
                    verified.get("code", "MODEL_REQUEST_FAILED"),
                    verified.get("reason", "Window verifier failed."),
                    details=verified,
                )
            if verified.get("status") != "accept":
                return self._review(
                    raw_path,
                    extraction,
                    verified.get("reason", "Window verifier rejected all candidates"),
                    verified,
                )
            idx = verified.get("candidate_index")
            try:
                candidate = extraction.candidates[int(idx)]
            except (ValueError, TypeError, IndexError):
                return self._error(
                    raw_path,
                    "window_verifier",
                    "MODEL_INVALID_RESPONSE",
                    "Window verifier returned an invalid candidate index.",
                    details=verified,
                )
            clean_html = candidate.html
            extraction_route = "small_ollama"
            extraction.selected_index = candidate.index
            extraction.reason = verified.get("reason") or extraction.reason
            extraction.confidence = self._number_or_default(verified.get("confidence"), extraction.confidence)

        task_classification = classify_task(
            clean_html,
            page_url=page_url or "",
            accepted_extensions=list(self.download_rules.get("accepted_extensions", [])),
        )
        if self._active_run_state:
            self._active_run_state.enter("TASK_DETECTION")
            self._active_run_state.finish("TASK_DETECTION", output={"mode": task_classification.mode.value, "response_mode": task_classification.response_mode.value, "confidence": task_classification.confidence})
        print_task_details(task_classification)

        if self.dry_run:
            print(f"[EXTRACT] {raw_path.name} -> {extraction_route} (confidence={extraction.confidence:.2f})")
            return {
                "status": "clean_ready",
                "clean_html": clean_html,
                "route": extraction_route,
                "task_classification": task_classification.as_dict(),
            }

        try:
            clean_path = save_clean_page(
                self.base_dir,
                raw_path,
                clean_html,
                overwrite=self.overwrite,
            )
        except OSError as exc:
            return self._error(raw_path, "save_clean", "FILE_IO", f"Could not write clean window page: {exc}")
        print(f"[CLEAN] {raw_path.name} -> {clean_path}")

        if screenshot_path is not None and screenshot_path.exists() and self.ocr_enabled:
            dependency_config = load_dependency_config(self.base_dir)
            tesseract_path = find_tesseract()
            if not tesseract_path:
                status = ensure_app("tesseract", "Tesseract OCR", dependency_config["tesseract"], prompt=True)
                if status.installed:
                    tesseract_path = find_tesseract()
            if tesseract_path:
                tess_cfg = dependency_config.get("tesseract", {}) if isinstance(dependency_config.get("tesseract"), dict) else {}
                managed_dir = self.base_dir / str(tess_cfg.get("managed_tessdata_dir", "data/tesseract"))
                languages_ok, language_detail = ensure_tesseract_languages(
                    tesseract_path,
                    tess_cfg.get("languages", ["eng", "fra"]),
                    base_dir=self.base_dir,
                    managed_tessdata_dir=managed_dir,
                    prompt=True,
                )
                print(f"[TESSERACT] {language_detail}", flush=True)
            else:
                print("[TESSERACT] OCR remains unavailable: Tesseract executable not found.", flush=True)

        dependency_config = load_dependency_config(self.base_dir)
        dependency_tesseract_cfg = dependency_config.get("tesseract", {}) if isinstance(dependency_config.get("tesseract"), dict) else {}
        managed_tessdata_dir = self.base_dir / str(dependency_tesseract_cfg.get("managed_tessdata_dir", "data/tesseract"))
        if not (managed_tessdata_dir / "tessdata").exists():
            managed_tessdata_dir = None

        screenshot_evidence = analyze_screenshot(
            self.base_dir,
            screenshot_path,
            ocr_enabled=self.ocr_enabled,
            ocr_executable=self.ocr_executable,
            ocr_language=self.ocr_language,
            ocr_tessdata_base_dir=managed_tessdata_dir,
            visual_analysis_enabled=self.visual_analysis_enabled and self.use_screenshot_with_vision,
            ollama=self.ollama,
            vision_model=self.vision_model,
        )
        print_screenshot_cli(screenshot_evidence)
        visual_analysis = screenshot_evidence.get("visual_analysis", {}) if isinstance(screenshot_evidence, dict) else {}
        if (
            isinstance(visual_analysis, dict)
            and visual_analysis.get("model")
            and self.ollama is not None
            and visual_analysis.get("status") in {"present", "error"}
        ):
            self._record_ollama_profile(
                stage="vision_model",
                model=str(visual_analysis.get("model")),
                filename=raw_path.name,
                input_chars=screenshot_path.stat().st_size if screenshot_path and screenshot_path.exists() else 0,
                compact_chars=None,
                request_chars=None,
                input_mode="screenshot_visual_analysis",
                result_status=str(visual_analysis.get("status")),
                error_code=None if visual_analysis.get("status") == "present" else "VISION_ANALYSIS_FAILED",
            )

        attachment_text = None
        attachment_metadata: dict[str, object] | None = None
        attachment_evidence: dict[str, object] | None = None
        if attachment_path is not None:
            try:
                if self._active_run_state:
                    self._active_run_state.enter("DOWNLOAD")
                download_result = process_download(
                    attachment_path,
                    self.base_dir,
                    rules=self.download_rules,
                    delete_source_after_handoff=self._is_transient_download_path(attachment_path),
                )
                content = download_result.content
                attachment_text = content.text
                attachment_metadata = content.metadata
                attachment_evidence = content.evidence
                self._analyze_sanitized_download_images(content)
                attachment_evidence = content.evidence
                if self._active_run_state:
                    self._active_run_state.set_hash(content.validation.file_sha256)
                    self._active_run_state.finish("DOWNLOAD", output={"sha256": content.validation.file_sha256, "detected_type": content.validation.detection.kind})
                backend = str(content.metadata.get("quarantine_backend", "unknown"))
                print(
                    f"[DOWNLOAD] Local attachment -> {backend} quarantine "
                    f"(job={content.metadata.get('quarantine_job_id', 'n/a')})",
                    flush=True,
                )
                print(
                    f"[DOWNLOAD] Accepted type={content.validation.effective_extension}; "
                    f"host_copy_deleted={content.metadata.get('host_source_deleted', False)}; "
                    f"extracted={len(content.text):,} chars",
                    flush=True,
                )
            except DownloadWorkflowError as exc:
                return self._error(
                    raw_path,
                    "download_ingestion",
                    exc.code,
                    str(exc),
                    details={
                        "quarantine_path": str(exc.quarantine_path) if exc.quarantine_path else None,
                        "rejected_path": str(exc.rejected_path) if exc.rejected_path else None,
                        "job_id": exc.job_id,
                        "details": getattr(exc, "details", None),
                    },
                )
            except OSError as exc:
                return self._error(
                    raw_path,
                    "download_quarantine",
                    "FILE_IO",
                    f"Could not quarantine the attachment safely: {exc}",
                )
        else:
            if self._active_run_state:
                self._active_run_state.enter("DOWNLOAD_DISCOVERY")
            candidates = find_download_candidates(
                clean_html,
                page_url or "",
                list(self.download_rules.get("accepted_extensions", [])),
            )
            candidate = select_download_candidate(candidates)
            if candidate is not None:
                if self._active_run_state:
                    self._active_run_state.finish("DOWNLOAD_DISCOVERY", output={"candidate_url": candidate.url, "candidate_label": candidate.label, "ambiguous": False})
                print(
                    f"[DOWNLOAD] Detected direct download: {candidate.label or candidate.url} -> {candidate.url}",
                    flush=True,
                )
                try:
                    if self._active_run_state:
                        self._active_run_state.enter("DOWNLOAD")
                    download_result, fetched = download_and_process(
                        candidate.url,
                        self.base_dir,
                        page_url=page_url or "",
                        rules=self.download_rules,
                    )
                    content = download_result.content
                    content.metadata.update({"candidate_label": candidate.label, "candidate_reason": candidate.reason})
                    attachment_text = content.text
                    attachment_metadata = content.metadata
                    attachment_evidence = content.evidence
                    self._analyze_sanitized_download_images(content)
                    attachment_evidence = content.evidence
                    if self._active_run_state:
                        self._active_run_state.set_hash(content.validation.file_sha256)
                        self._active_run_state.finish("DOWNLOAD", output={"size": fetched.size, "sha256": fetched.sha256, "detected_type": content.validation.detection.kind})
                    print(
                        f"[DOWNLOAD] Fetched={fetched.size:,} bytes type={fetched.content_type} "
                        f"detected={content.validation.effective_extension}; extracted={len(content.text):,} chars",
                        flush=True,
                    )
                except DownloadWorkflowError as exc:
                    return self._error(
                        raw_path,
                        "download_ingestion",
                        exc.code,
                        str(exc),
                        details={
                            "download_url": candidate.url,
                            "label": candidate.label,
                            "quarantine_path": str(exc.quarantine_path) if exc.quarantine_path else None,
                            "rejected_path": str(exc.rejected_path) if exc.rejected_path else None,
                            "job_id": exc.job_id,
                            "details": getattr(exc, "details", None),
                        },
                    )
            elif page_may_require_download(clean_html):
                details = [
                    {"url": item.url, "label": item.label, "score": item.score, "reason": item.reason}
                    for item in candidates[:5]
                ]
                return self._error(
                    raw_path,
                    "download_detection",
                    "DOWNLOAD_LINK_NOT_FOUND",
                    "The question appears to require a downloadable file, but no unambiguous direct download URL was found.",
                    details={"candidates": details},
                )

        if not self.use_answer_llm:
            return {
                "status": "clean_ready",
                "clean_path": str(clean_path),
                "task_classification": task_classification.as_dict(),
            }

        decision = None
        if self._active_run_state:
            self._active_run_state.enter("TASK_SOLVING")
        try:
            deterministic = solve_deterministic_task(
                clean_html,
                filename=raw_path.name,
                page_url=page_url,
                title=title,
                attachment_text=attachment_text,
                attachment_metadata=attachment_metadata,
                max_representation_chars=self.compact_answer_chars,
            )
        except Exception as exc:
            deterministic = None
            print(f"[DETERMINISTIC] Solver skipped after an internal error: {exc}", flush=True)

        if deterministic is not None:
            decision = deterministic.as_dict(
                filename=raw_path.name,
                input_mode="deterministic",
            )
            print(
                f"[DETERMINISTIC] {deterministic.solver}: {deterministic.operation}",
                flush=True,
            )
        else:
            if not self.use_answer_llm:
                return {
                    "status": "clean_ready",
                    "clean_path": str(clean_path),
                    "task_classification": task_classification.as_dict(),
                }

            if not self.ollama:
                return self._error(
                    raw_path,
                    "answer_model",
                    "OLLAMA_UNAVAILABLE",
                    "Answer model was requested but no Ollama client is configured.",
                )

            decision = self._answer(
                clean_html,
                raw_path,
                page_url=page_url,
                title=title,
                attachment_text=attachment_text,
                attachment_metadata=attachment_metadata,
                attachment_evidence=attachment_evidence,
                screenshot_path=screenshot_path,
                screenshot_evidence=screenshot_evidence,
            )
        if self._active_run_state:
            task_status = "FAILED" if decision.get("status") == "error" else "SUCCESS"
            self._active_run_state.finish(
                "TASK_SOLVING",
                status=task_status,
                output={"status": decision.get("status"), "solver": decision.get("solver") or decision.get("model")},
                error_code=decision.get("code") if task_status != "SUCCESS" else None,
                error_message=decision.get("reason") if task_status != "SUCCESS" else None,
            )
        decision["task_mode"] = task_classification.mode.value
        decision["task_response_mode"] = task_classification.response_mode.value
        decision["task_classification_confidence"] = task_classification.confidence
        decision["screenshot_evidence"] = screenshot_evidence
        print_answer_result(decision)

        if decision.get("status") == "error":
            return self._error(
                raw_path,
                "answer_model",
                decision.get("code", "MODEL_REQUEST_FAILED"),
                decision.get("reason", "Answer model failed."),
                details=decision,
            )

        if self._active_run_state:
            self._active_run_state.enter("ANSWER_VALIDATION")
            self._active_run_state.finish("ANSWER_VALIDATION", output={"status": decision.get("status"), "answer": decision.get("answer"), "validated_confidence": decision.get("validated_confidence")})

        destination = ANSWER_FOLDER if decision.get("status") == "answer" else ANSWER_NOT_FOUND_FOLDER

        if self._active_run_state:
            self._active_run_state.enter("FINAL_ANSWER")

        try:
            json_path = save_answer_json(
                self.base_dir,
                clean_path,
                decision,
                overwrite=self.overwrite,
            )
        except OSError as exc:
            return self._error(raw_path, "save_answer", "FILE_IO", f"Could not write answer JSON: {exc}")

        if self._active_run_state:
            self._active_run_state.finish("FINAL_ANSWER", output={"status": decision.get("status"), "decision_path": str(json_path)})

        if self._active_run_state:
            self._active_run_state.enter("CLEANUP")
        download_cleanup = self._maybe_cleanup_download(
            attachment_metadata=attachment_metadata,
            decision=decision,
            raw_path=raw_path,
        )
        if download_cleanup:
            decision["download_cleanup"] = download_cleanup
        if self._active_run_state:
            self._active_run_state.finish("CLEANUP", output=download_cleanup or {"action": "none"})

        if download_cleanup:
            try:
                save_answer_json(
                    self.base_dir,
                    clean_path,
                    decision,
                    overwrite=True,
                )
            except OSError as exc:
                return self._error(raw_path, "save_answer_cleanup_metadata", "FILE_IO", f"Could not update answer JSON with cleanup state: {exc}")

        if decision.get("status") == "answer" and decision.get("answer") is not None:
            # Keep the final answer as the final user-facing console line.
            print_final_answer(decision["answer"])

        return {
            "status": decision.get("status"),
            "clean_path": str(clean_path),
            "decision_path": str(json_path),
            "decision": decision,
            "task_classification": task_classification.as_dict(),
            "pipeline_state": self._active_run_state.as_dict() if self._active_run_state else None,
        }


    def _analyze_sanitized_download_images(self, content) -> dict[str, object]:
        """Run optional vision reasoning only on Docker-produced normalized PNG artifacts."""
        safe_artifacts = tuple(getattr(content, "safe_image_artifacts", ()) or ())
        evidence = content.evidence
        section: dict[str, object] = {"status": "non-existent", "items": []}
        if not safe_artifacts:
            evidence["visual_analysis"] = section
            return section
        if not bool(self.download_rules.get("download_image_visual_analysis", True)):
            section["status"] = "disabled"
            evidence["visual_analysis"] = section
            return section
        if self.ollama is None or not self.vision_model:
            section["status"] = "unavailable"
            evidence["visual_analysis"] = section
            return section
        max_images = int(self.download_rules.get("max_visual_images", 8))
        items: list[dict[str, object]] = []
        image_metadata = content.metadata.get("image_analysis", [])
        for index, artifact in enumerate(safe_artifacts[:max_images]):
            artifact_hash = hashlib.sha256(artifact).hexdigest()
            source_name = None
            if isinstance(image_metadata, list) and index < len(image_metadata) and isinstance(image_metadata[index], dict):
                source_name = image_metadata[index].get("name")
            with tempfile.TemporaryDirectory(prefix="scrapper-safe-vision-") as temp_dir:
                image_path = Path(temp_dir) / f"{artifact_hash}.png"
                image_path.write_bytes(artifact)
                visual = run_visual_analysis(image_path, ollama=self.ollama, model=self.vision_model)
            items.append({
                "status": visual.get("status"),
                "model": visual.get("model"),
                "summary": visual.get("summary"),
                "observations": visual.get("observations"),
                "confidence": visual.get("confidence"),
                "source_name": source_name,
                "safe_artifact_sha256": artifact_hash,
                "source": "docker_normalized_png",
            })
        section["status"] = "present" if any(item.get("status") == "present" for item in items) else ("error" if any(item.get("status") == "error" for item in items) else "unavailable")
        section["items"] = items
        evidence["visual_analysis"] = section
        content.metadata["visual_analysis"] = section
        try:
            save_processing_metadata(self.base_dir, content)
        except OSError as exc:
            print(f"[DOWNLOAD VISUAL EVIDENCE] Could not persist visual evidence: {exc}", flush=True)
        return section


    def _is_transient_download_path(self, path: Path) -> bool:
        """Return whether a host-side attachment is an application-managed transient download."""
        try:
            candidate = Path(path).resolve()
            incoming = (self.base_dir / "data" / "downloads" / "incoming").resolve()
            return candidate.is_relative_to(incoming)
        except OSError:
            return False

    def _maybe_cleanup_download(
        self,
        *,
        attachment_metadata: dict[str, object] | None,
        decision: dict[str, Any],
        raw_path: Path,
    ) -> dict[str, object] | None:
        """Apply the quarantine-retention policy using the strict confidence > threshold rule."""
        if not attachment_metadata:
            return None
        state = str(attachment_metadata.get("processing_state", "accepted")).lower()
        if decision.get("status") != "answer":
            return {"action": "kept", "reason": "answer_not_found", "processing_state": state}

        rules = self.download_rules
        enabled = bool(rules.get("delete_download_after_high_confidence", True))
        threshold = float(rules.get("download_delete_confidence_threshold", 0.9))
        confidence = self._number_or_default(decision.get("validated_confidence", decision.get("confidence")), -1.0)
        quarantine_path = attachment_metadata.get("quarantine_path")

        if not enabled:
            return {"action": "kept", "reason": "disabled", "confidence": confidence, "threshold": threshold}
        if state in {"error", "inconclusive", "rejected", "unsupported"}:
            return {"action": "kept", "reason": state, "confidence": confidence, "threshold": threshold}
        if confidence <= threshold:
            print(
                f"[DOWNLOAD CLEANUP] Keeping quarantine artifact: confidence={confidence:.2f} "
                f"is not above threshold={threshold:.2f}.",
                flush=True,
            )
            return {"action": "kept", "reason": "confidence_threshold", "confidence": confidence, "threshold": threshold}
        if not quarantine_path:
            return {"action": "kept", "reason": "missing_quarantine_path", "confidence": confidence, "threshold": threshold}

        try:
            deleted = delete_quarantined_download(attachment_metadata)
        except (OSError, DownloadWorkflowError, DownloadIngestionError) as exc:
            payload = error_payload(
                "DOWNLOAD_CLEANUP_FAILED",
                "CLEANUP",
                str(exc),
                file=raw_path.name,
                operation="delete_quarantine_artifact",
                details={"quarantine_path": str(quarantine_path), "confidence": confidence, "threshold": threshold},
                status="FAILED",
                exception_type=type(exc).__name__,
            )
            print(format_error(payload))
            if not self.dry_run:
                try:
                    path = save_error_report(self.base_dir, f"{raw_path.stem}_download_cleanup", payload, overwrite=self.overwrite)
                    print(f"[ERROR REPORT] {path}")
                except OSError as save_exc:
                    print(f"[ERROR REPORT FAILED] {save_exc}")
            return {"action": "delete_failed", "reason": str(exc), "confidence": confidence, "path": str(quarantine_path)}

        if deleted:
            print(
                f"[DOWNLOAD CLEANUP] Deleted quarantine artifact after high-confidence answer "
                f"({confidence:.2f} > {threshold:.2f})",
                flush=True,
            )
            return {"action": "deleted", "confidence": confidence, "threshold": threshold, "path": str(quarantine_path)}
        return {"action": "kept", "reason": "artifact_not_found", "confidence": confidence, "path": str(quarantine_path)}

    def _verify_candidates(self, candidates: list[Candidate], filename: str, raw_path: Path) -> dict[str, Any]:
        """Ask the small Ollama model to accept or reject ambiguous HTML candidates."""
        if not candidates:
            return {"status": "reject", "candidate_index": None, "confidence": 0.0, "reason": "No candidates."}

        items = []
        for candidate in candidates:
            snippet = candidate.html[:DEFAULT_MAX_CANDIDATE_CHARS]
            items.append({
                "index": candidate.index,
                "tag": candidate.tag_name,
                "path": candidate.path,
                "score": round(candidate.score, 2),
                "text": candidate.text[:DEFAULT_MAX_CANDIDATE_CHARS],
                "html": snippet,
            })

        user_content = json.dumps({"filename": filename, "candidates": items}, ensure_ascii=False, indent=2)
        try:
            result = self.ollama.chat_json(
                self.window_model,
                window_verifier_prompt(),
                user_content,
            )
            self._record_ollama_profile(
                stage="window_verifier",
                model=self.window_model,
                filename=filename,
                input_chars=len(user_content),
                compact_chars=None,
                result_status=result.get("status") if isinstance(result, dict) else None,
            )
        except ModelNotInstalledError as exc:
            self._handle_missing_model(raw_path, exc, stage="window_verifier")
            return {"status": "error", "code": exc.code, "reason": str(exc)}
        except InvalidOllamaResponseError as exc:
            return {"status": "error", "code": exc.code, "reason": str(exc)}
        except OllamaError as exc:
            return {"status": "error", "code": exc.code, "reason": str(exc)}

        status = str(result.get("status", "reject")).lower()
        if status not in {"accept", "reject"}:
            return {"status": "error", "code": "MODEL_INVALID_RESPONSE", "reason": "Window verifier returned an invalid status."}

        confidence = self._number_or_default(result.get("confidence"), 0.0)
        if not 0.0 <= confidence <= 1.0:
            return {"status": "error", "code": "MODEL_INVALID_RESPONSE", "reason": "Window verifier returned confidence outside 0..1."}

        return {
            "status": status,
            "candidate_index": result.get("candidate_index"),
            "confidence": confidence,
            "reason": str(result.get("reason", "")),
        }

    def _answer(
        self,
        clean_html: str,
        raw_path: Path,
        page_url: str | None,
        title: str | None,
        attachment_text: str | None = None,
        attachment_metadata: dict[str, object] | None = None,
        attachment_evidence: dict[str, object] | None = None,
        screenshot_path: Path | None = None,
        screenshot_evidence: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Send the cleaned page and optional safely extracted attachment text to Ollama."""
        filename = raw_path.name
        full_content = clean_html
        truncated = False
        if len(full_content) > self.max_answer_chars:
            full_content = full_content[: self.max_answer_chars]
            truncated = True

        if self.answer_input_mode == "full":
            if attachment_text is None:
                model_content = full_content
            else:
                model_content = json.dumps(
                    {
                        "clean_html": full_content,
                        "attachment": {
                            "metadata": attachment_metadata,
                            "evidence": attachment_evidence,
                            "text": attachment_text,
                        },
                    },
                    ensure_ascii=False,
                )
            input_mode = "full_clean_html" if attachment_text is None else "full_clean_html_plus_attachment"
            compact_chars = None
        else:
            compact = build_compact_representation(
                full_content,
                filename=filename,
                page_url=page_url,
                title=title,
                max_text_chars=self.compact_answer_chars,
                attachment_text=attachment_text,
                attachment_metadata=attachment_metadata,
                attachment_evidence=attachment_evidence,
            )
            model_content = compact["model_text"]
            input_mode = "compact_question_representation"
            compact_chars = len(model_content)

        user_payload = {
            "filename": filename,
            "page_url": page_url,
            "title": title,
            "input_mode": input_mode,
            "source_truncated": truncated,
            "source_clean_html_chars": len(full_content),
            "model_input": model_content,
            "attachment_present": attachment_text is not None,
            "attachment_summary": summarize_attachment_metadata(attachment_metadata),
            "attachment_evidence": attachment_evidence,
            "screenshot_evidence": screenshot_evidence,
        }
        user_content = json.dumps(user_payload, ensure_ascii=False)

        choice_context = build_compact_representation(
            clean_html,
            filename=filename,
            page_url=page_url,
            title=title,
            max_text_chars=max(4000, min(self.compact_answer_chars, 12000)),
            attachment_text=None,
            attachment_metadata=None,
        )
        task_data = choice_context.get("task") if isinstance(choice_context, dict) else None
        choices = task_data.get("choices", []) if isinstance(task_data, dict) else []

        print(
            f"[OLLAMA] Preparing answer input: mode={input_mode}, "
            f"clean HTML={len(full_content):,} chars, model payload={len(user_content):,} chars",
            flush=True,
        )

        model_for_answer = self.answer_model
        print(
            f"[ANSWER INPUT] task={task_data.get('exact_task') if isinstance(task_data, dict) else '(unknown)'} ",
            flush=True,
        )
        print(
            f"[ANSWER INPUT] choices={len(choices) if isinstance(choices, list) else 0} ",
            f"attachment={attachment_text is not None} screenshot={screenshot_evidence.get('screenshot', {}).get('status') if screenshot_evidence else 'non-existent'}",
            flush=True,
        )

        try:
            result = self.ollama.chat_json(
                model_for_answer,
                answerer_prompt(),
                user_content,
            )
            self._record_ollama_profile(
                stage="answer_model",
                model=model_for_answer,
                filename=filename,
                input_chars=len(full_content),
                compact_chars=compact_chars,
                request_chars=len(user_content),
                input_mode=input_mode + ("+screenshot_evidence" if screenshot_evidence and screenshot_evidence.get("screenshot", {}).get("status") == "present" else ""),
                result_status=result.get("status") if isinstance(result, dict) else None,
            )
        except ModelNotInstalledError as exc:
            self._record_ollama_profile(
                stage="answer_model", model=model_for_answer, filename=filename,
                input_chars=len(full_content), compact_chars=compact_chars,
                request_chars=len(user_content), input_mode=input_mode,
                error_code=exc.code,
            )
            self._handle_missing_model(raw_path, exc, stage="answer_model")
            return {"status": "error", "code": exc.code, "confidence": 0.0, "answer": None, "reason": str(exc)}
        except InvalidOllamaResponseError as exc:
            self._record_ollama_profile(
                stage="answer_model", model=model_for_answer, filename=filename,
                input_chars=len(full_content), compact_chars=compact_chars,
                request_chars=len(user_content), input_mode=input_mode,
                error_code=exc.code,
            )
            return {"status": "error", "code": exc.code, "confidence": 0.0, "answer": None, "reason": str(exc)}
        except OllamaError as exc:
            self._record_ollama_profile(
                stage="answer_model", model=model_for_answer, filename=filename,
                input_chars=len(full_content), compact_chars=compact_chars,
                request_chars=len(user_content), input_mode=input_mode,
                error_code=exc.code,
            )
            return {"status": "error", "code": exc.code, "confidence": 0.0, "answer": None, "reason": str(exc)}

        status = str(result.get("status", "")).lower()
        if status not in {"answer", "answer_not_found"}:
            return {
                "status": "error",
                "code": "MODEL_INVALID_RESPONSE",
                "confidence": 0.0,
                "answer": None,
                "reason": f"Answer model returned unsupported status: {status!r}",
            }

        confidence = self._number_or_default(result.get("confidence"), -1.0)
        if not 0.0 <= confidence <= 1.0:
            return {
                "status": "error",
                "code": "MODEL_INVALID_RESPONSE",
                "confidence": 0.0,
                "answer": None,
                "reason": "Answer model returned confidence outside 0..1.",
            }

        choice_resolution = None
        choice_index = result.get("choice_index")
        if choice_index is not None:
            if isinstance(choice_index, bool) or not isinstance(choice_index, int) or choice_index < 1:
                return {
                    "status": "error",
                    "code": "MODEL_INVALID_CHOICE_RESPONSE",
                    "confidence": confidence,
                    "answer": None,
                    "reason": "Answer model returned an invalid choice_index.",
                    "model_choice_index": choice_index,
                }
            visible_choices = [item for item in choices if isinstance(item, dict)] if isinstance(choices, list) else []
            if not visible_choices or choice_index > len(visible_choices):
                return {
                    "status": "error",
                    "code": "MODEL_INVALID_CHOICE_RESPONSE",
                    "confidence": confidence,
                    "answer": None,
                    "reason": "Answer model returned a choice_index that does not correspond to a visible choice.",
                    "model_choice_index": choice_index,
                }
            choice_resolution = resolve_choice_answer(str(choice_index), visible_choices)
            if choice_resolution.get("status") == "invalid":
                return {
                    "status": "error",
                    "code": "MODEL_INVALID_CHOICE_RESPONSE",
                    "confidence": confidence,
                    "answer": None,
                    "reason": str(choice_resolution.get("reason", "Could not resolve the selected choice.")),
                }

        answer = result.get("answer")
        if status == "answer" and not answer and choice_resolution is None:
            return {
                "status": "error",
                "code": "MODEL_INVALID_RESPONSE",
                "confidence": confidence,
                "answer": None,
                "reason": "Answer model claimed to have an answer but returned no answer text or choice index.",
            }

        if status == "answer" and choices and isinstance(choices, list):
            visible_choices = [item for item in choices if isinstance(item, dict)]
            answer_resolution = resolve_choice_answer(answer, visible_choices) if answer is not None else {"status": "unchanged", "answer": ""}
            if answer_resolution.get("status") == "resolved":
                if choice_resolution is not None and answer_resolution.get("answer") != choice_resolution.get("answer"):
                    return {
                        "status": "error",
                        "code": "MODEL_INVALID_CHOICE_RESPONSE",
                        "confidence": confidence,
                        "answer": None,
                        "reason": "choice_index and answer text identify different visible choices.",
                    }
                answer = answer_resolution["answer"]
                choice_resolution = choice_resolution or answer_resolution
            elif answer_resolution.get("status") == "invalid":
                return {
                    "status": "error",
                    "code": "MODEL_INVALID_CHOICE_RESPONSE",
                    "confidence": confidence,
                    "answer": None,
                    "reason": str(answer_resolution.get("reason", "Could not resolve the selected choice.")),
                    "model_answer": str(answer),
                    "model": model_for_answer,
                    "filename": filename,
                    "input_mode": input_mode,
                    "input_chars": len(full_content),
                    "model_input_chars": len(model_content),
                }
            elif choice_resolution is not None and choice_resolution.get("status") == "resolved":
                answer = choice_resolution["answer"]
            elif answer is not None:
                answer = str(answer).strip()

        validated_confidence = confidence
        validation_source = "deterministic_choice_validation" if choice_resolution and choice_resolution.get("status") == "resolved" else "model_response_validated"

        result_payload = {
            "status": status,
            "confidence": confidence,
            "validated_confidence": validated_confidence,
            "confidence_source": validation_source,
            "answer": str(answer) if answer is not None else None,
            "reason": str(result.get("reason", "")),
            "model": model_for_answer,
            "filename": filename,
            "input_mode": input_mode,
            "input_chars": len(full_content),
            "model_input_chars": len(model_content),
        }
        if choice_resolution is not None and choice_resolution.get("status") == "resolved":
            result_payload["model_answer"] = str(result.get("answer")) if result.get("answer") is not None else None
            result_payload["model_choice_index"] = choice_index
            result_payload["answer_normalization"] = choice_resolution
        return result_payload

    def _record_ollama_profile(
        self,
        *,
        stage: str,
        model: str,
        filename: str,
        input_chars: int,
        compact_chars: int | None,
        request_chars: int | None = None,
        input_mode: str | None = None,
        result_status: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Record and print timing and token metrics for one Ollama request."""
        metrics = normalize_ollama_metrics(
            getattr(self.ollama, "last_metrics", {}),
            wall_ms=(getattr(self.ollama, "last_metrics", {}) or {}).get("client_wall_seconds", 0) * 1000
            if getattr(self.ollama, "last_metrics", None) else None,
        )
        print_metrics(stage, model, metrics, input_chars, compact_chars)
        payload = {
            "stage": stage,
            "model": model,
            "file": filename,
            "input_mode": input_mode,
            "source_input_chars": input_chars,
            "compact_input_chars": compact_chars,
            "request_payload_chars": request_chars,
            "result_status": result_status,
            "error_code": error_code,
            "metrics": metrics,
        }
        try:
            path = save_profile(self.base_dir, payload, overwrite=False)
            print(f"[OLLAMA PROFILE] {path}", flush=True)
        except OSError as exc:
            print(f"[OLLAMA PROFILE FAILED] {exc}", flush=True)

    def _handle_missing_model(self, raw_path: Path, exc: ModelNotInstalledError, *, stage: str) -> None:
        """Report a missing model and invoke the configured install/restart handler."""
        payload = error_payload(exc.code, stage, str(exc), file=raw_path.name)
        print(format_error(payload))
        if not self.dry_run:
            try:
                path = save_error_report(self.base_dir, f"{raw_path.stem}_{stage}", payload, overwrite=self.overwrite)
                print(f"[ERROR REPORT] {path}")
            except OSError as save_exc:
                print(f"[ERROR REPORT FAILED] {save_exc}")

        if self.missing_model_handler is None:
            raise RuntimeError("No missing-model handler is configured.")

        model = self.window_model if stage == "window_verifier" else self.answer_model
        installed = self.missing_model_handler(model, stage, raw_path.name)
        if installed:
            # The handler installs and restarts the process on success. This line is
            # only reachable when a custom handler explicitly reports success.
            return
        print("[STOPPED] The required Ollama model was not installed.")
        raise SystemExit(1)

    @staticmethod
    def _number_or_default(value: object, default: float) -> float:
        """Convert a value to float, returning the fallback when conversion fails."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _review(self, raw_path: Path, extraction: ExtractionResult, extra: str, verifier: dict[str, Any] | None = None) -> dict[str, Any]:
        """Write or display a review record when the target window is not trusted."""
        payload = {
            "status": "window_not_found",
            "file": raw_path.name,
            "reason": f"{extraction.reason} {extra}".strip(),
            "deterministic_confidence": extraction.confidence,
            "candidates": [
                {
                    "index": c.index,
                    "tag": c.tag_name,
                    "path": c.path,
                    "score": c.score,
                    "text_preview": c.text[:1000],
                }
                for c in extraction.candidates
            ],
            "verifier": verifier,
        }
        if not self.dry_run:
            path = save_review(self.base_dir, raw_path.stem, payload, overwrite=self.overwrite)
            print(f"[REVIEW] {raw_path.name} -> {path.name}")
        else:
            print(f"[REVIEW] {raw_path.name}: {payload['reason']}")
        return payload

    def _error(self, raw_path: Path, stage: str, code: str, message: str, details: object = None) -> dict[str, Any]:
        """Create, report, and return a structured pipeline error while preserving the first failing stage."""
        normalized_stage = {
            "read_raw": "CAPTURE", "window_extraction": "TASK_DETECTION", "window_verifier": "TASK_DETECTION",
            "download_detection": "DOWNLOAD_DISCOVERY", "download_ingestion": "SECURITY_VALIDATION", "download_quarantine": "QUARANTINE",
            "answer_model": "ANSWER_VALIDATION", "save_answer": "FINAL_ANSWER",
        }.get(stage, str(stage).upper())
        if self._active_run_state:
            self._active_run_state.fail(normalized_stage, code, message, operation=stage)
        payload = error_payload(
            code, stage, message, file=raw_path.name, details=details,
            request_id=self._active_run_state.request_id if self._active_run_state else None,
            file_sha256=self._active_run_state.file_sha256 if self._active_run_state else None,
            status="FAILED",
        )
        if self._active_run_state:
            payload["pipeline_state"] = self._active_run_state.as_dict()
        print(format_error(payload))
        if not self.dry_run:
            try:
                path = save_error_report(self.base_dir, f"{raw_path.stem}_{stage}", payload, overwrite=self.overwrite)
                print(f"[ERROR REPORT] {path}")
            except OSError as exc:
                print(f"[ERROR REPORT FAILED] {exc}")
        return {**payload, "status": "error", "pipeline_status": payload.get("status", "FAILED"), "processing_state": "ERROR"}
