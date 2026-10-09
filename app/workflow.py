import argparse
import os
from pathlib import Path

from .config import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_WINDOW_MODEL,
    data_dir,
    default_base_dir,
    load_vision_settings,
)
from .errors.handler import error_payload, format_error, save_error_report
from .ollama.client import OllamaClient, OllamaError
from .ollama.installer import prompt_install_and_restart
from .dependency_installer import ensure_all_dependencies, DependencyInstallError
from .pipeline import Pipeline
from .downloads.detector import detect_file_type
from .downloads.rules import load_download_rules
from .window.extractor import WindowExtractor
from .window.rules import load_rules


def _pipeline_from_args(args: argparse.Namespace, base_dir: Path) -> Pipeline:
    """Create a configured pipeline from parsed command-line arguments."""
    rules = load_rules()
    extractor = WindowExtractor(rules)
    vision_settings = load_vision_settings(base_dir)
    ollama = None
    if not args.no_window_llm or not args.no_answer_llm:
        ollama = OllamaClient(args.ollama_host, timeout=args.ollama_timeout)

    return Pipeline(
        base_dir=base_dir,
        extractor=extractor,
        ollama=ollama,
        window_model=args.window_model,
        answer_model=args.answer_model,
        vision_model=args.vision_model,
        use_screenshot_with_vision=not args.no_screenshot,
        ocr_enabled=bool(vision_settings.get("ocr_enabled", True)) and not args.no_ocr and not args.no_screenshot,
        ocr_executable=str(vision_settings.get("ocr_executable", "tesseract")),
        ocr_language=args.ocr_language,
        visual_analysis_enabled=bool(vision_settings.get("visual_analysis_enabled", True)) and not args.no_visual_analysis,
        use_window_llm=not args.no_window_llm,
        use_answer_llm=not args.no_answer_llm,
        max_answer_chars=args.max_answer_chars,
        answer_input_mode=args.answer_input_mode,
        compact_answer_chars=args.compact_answer_chars,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        download_rules=load_download_rules(),
        missing_model_handler=lambda model, stage, resume_file: prompt_install_and_restart(
            model, stage, resume_file
        ),
    )


def _print_model_status(ollama: OllamaClient | None, args: argparse.Namespace) -> None:
    """Print Ollama server and configured-model availability."""
    if ollama is None:
        return
    try:
        models = ollama.list_models()
    except OllamaError as exc:
        payload = error_payload(
            exc.code, "ollama_startup_check", str(exc),
            details={"exception": exc.__class__.__name__},
        )
        print(format_error(payload))
        print("[OLLAMA] Continuing; model calls will report the same error if reached.")
        return

    print(f"[OLLAMA] Server OK: {ollama.host}")
    print(f"[OLLAMA] Installed models: {', '.join(models) if models else '(none)'}")
    for label, model, needed in [
        ("window verifier", args.window_model, not args.no_window_llm),
        ("answer", args.answer_model, not args.no_answer_llm),
        ("vision", args.vision_model, bool(args.vision_model) and not args.no_screenshot and not args.no_answer_llm),
    ]:
        if not needed:
            continue
        installed = model in models or (":" not in model and f"{model}:latest" in models)
        if installed:
            print(f"[OLLAMA] {label} model OK: {model}")
        else:
            print(f"[OLLAMA] {label} model MISSING: {model}")
            print(f"         Install with: ollama pull {model}")
            if label == "answer":
                print("         Clean pages will be kept in clean_window_pages until the model is available.")
            else:
                print("         Ambiguous extractions will be kept in review/errors; raw pages remain untouched.")


def _inspect_download(path: Path) -> None:
    """Print Python-based signature and archive inspection for one downloaded file."""
    rules = load_download_rules()
    detection = detect_file_type(path, rules)
    print(f"[DOWNLOAD INSPECT] {path.name}")
    print(f"  detected kind: {detection.kind}")
    print(f"  effective extension: {detection.canonical_extension}")
    print(f"  MIME: {detection.mime_type or 'unknown'}")
    print(f"  signature: {detection.signature}")
    if detection.archive_entries:
        print(f"  archive entries: {len(detection.archive_entries)}")
        for entry in detection.archive_entries[:100]:
            print(f"    - {entry.name} ({entry.uncompressed_size} bytes)")
            if entry.text_preview:
                preview = entry.text_preview.replace("\n", "\n      ")
                print(f"      preview: {preview[:500]}")
        if len(detection.archive_entries) > 100:
            print("    ... output truncated at 100 entries")
    if detection.archive_kind:
        print(f"  recognized container: {detection.archive_kind}")
    elif detection.archive_entries:
        print("  recognized container: unsupported ZIP")


def _process_existing(args: argparse.Namespace, base_dir: Path, pipeline: Pipeline) -> None:
    """Process raw HTML files already stored under data/raw_pages."""
    raw_dir = data_dir(base_dir) / "raw_pages"
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(raw_dir.glob("*.html"))
    if args.file:
        candidate = raw_dir / args.file
        paths = [candidate] if candidate.exists() else []
    if not paths:
        print("No raw HTML pages found to process.")
        return

    capture_url = args.url or os.environ.get("SCRAPPER_CAPTURE_URL", "")
    capture_title = args.title or os.environ.get("SCRAPPER_CAPTURE_TITLE", "")
    for path in paths:
        page_url = capture_url if args.file else None
        title = capture_title if args.file else None
        attachment = Path(args.attachment) if args.attachment else None
        screenshot_env = os.environ.get("SCRAPPER_SCREENSHOT_PATH", "") if args.file else ""
        screenshot = Path(screenshot_env) if screenshot_env else None
        pipeline.process_raw_file(
            path,
            page_url=page_url,
            title=title,
            attachment_path=attachment,
            screenshot_path=screenshot,
        )


def run(args: argparse.Namespace) -> None:
    """Run model checks, process existing captures, or display capture instructions."""
    base_dir = default_base_dir()
    pipeline = _pipeline_from_args(args, base_dir)

    if args.inspect_download:
        _inspect_download(Path(args.inspect_download))
        return

    if args.setup_dependencies:
        try:
            statuses = ensure_all_dependencies(base_dir, prompt=True, include_models=True)
        except DependencyInstallError as exc:
            print(f"[SETUP FAILED] {exc}")
            return
        failed = [status for status in statuses if not status.installed]
        for status in statuses:
            state = "OK" if status.installed else "MISSING"
            print(f"[SETUP] {state:<7} {status.label}: {status.detail}")
        try:
            from .dependency_installer import collect_dependency_diagnostics
            import json as _json
            report_path = base_dir / "data" / "dependency_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(_json.dumps(collect_dependency_diagnostics(base_dir), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[SETUP] Dependency health report: {report_path}")
        except OSError as exc:
            print(f"[SETUP] Could not write dependency health report: {exc}")
        if not failed:
            try:
                from .native_messaging.install_native_host import install as install_native_host
                install_native_host()
            except Exception as exc:
                print(f"[NATIVE HOST] Setup could not be completed automatically: {exc}")
        return

    if args.check_models:
        _print_model_status(pipeline.ollama, args)
        return

    _print_model_status(pipeline.ollama, args)

    if args.process_existing:
        _process_existing(args, base_dir, pipeline)
        return

    print("[CAPTURE] Firefox capture is extension-triggered.")
    print("[CAPTURE] Click the Scrapper Ollama Firefox toolbar button on the tab you want to capture.")
    print("[CAPTURE] No localhost bridge or listening port is used.")
