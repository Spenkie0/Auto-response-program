from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .storage.writer import save_raw_page, save_screenshot


def project_root() -> Path:
    """Return the root directory of the scraper project."""
    return Path(__file__).resolve().parent.parent


def _is_windows() -> bool:
    """Return whether the current runtime is Windows."""
    return os.name == "nt"


def _windows_creation_flags() -> int:
    """Return Windows flags used to launch the pipeline in its own console."""
    if not _is_windows():
        return 0
    return (
        getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def build_pipeline_launch_command(raw_path: Path) -> list[str]:
    """Build the platform-neutral command used to start the processing pipeline."""
    root = project_root()
    main_py = root / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"Pipeline entry point not found: {main_py}")
    return [
        str(Path(sys.executable).resolve()),
        "-u",
        str(main_py),
        "--process-existing",
        "--file",
        raw_path.name,
    ]


def launch_pipeline(raw_path: Path, *, url: str = "", title: str = "", screenshot_path: Path | None = None) -> None:
    """Start the pipeline in a persistent Windows console or the current console on other systems."""
    root = project_root()
    command = build_pipeline_launch_command(raw_path)

    # Capture metadata is passed through the child environment instead of the
    # Windows command line. This prevents CMD metacharacters such as |, &, <,
    # and > in page titles or URLs from being interpreted as shell syntax.
    child_env = os.environ.copy()
    child_env["SCRAPPER_CAPTURE_URL"] = url
    child_env["SCRAPPER_CAPTURE_TITLE"] = title
    if screenshot_path is not None:
        child_env["SCRAPPER_SCREENSHOT_PATH"] = str(screenshot_path)

    creationflags = _windows_creation_flags()
    launch = command
    if _is_windows():
        # /K deliberately keeps the one visible pipeline console alive after
        # the Python process exits, including across a model-install restart.
        inner_command = subprocess.list2cmdline(command)
        launch = ["cmd.exe", "/d", "/k", inner_command]

    subprocess.Popen(
        launch,
        cwd=str(root),
        creationflags=creationflags,
        close_fds=False,
        env=child_env,
    )


def capture_and_launch(payload: dict[str, object]) -> dict[str, object]:
    """Validate, save, and process one Firefox native-messaging capture payload."""
    if not isinstance(payload, dict):
        raise ValueError("Native capture payload must be a JSON object.")
    if payload.get("action") != "capture":
        raise ValueError(f"Unsupported native action: {payload.get('action')!r}")

    html = payload.get("html")
    if not isinstance(html, str) or not html:
        raise ValueError("Capture payload does not contain HTML.")

    url = str(payload.get("url") or "")
    title = str(payload.get("title") or "page")
    root = project_root()
    raw_path = save_raw_page(root, title, html)

    screenshot_path = None
    screenshot_data_url = payload.get("screenshot_data_url")
    if screenshot_data_url:
        try:
            screenshot_path = save_screenshot(root, raw_path.stem, str(screenshot_data_url))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Could not save Firefox screenshot: {exc}") from exc

    launch_pipeline(raw_path, url=url, title=title, screenshot_path=screenshot_path)
    return {
        "ok": True,
        "status": "captured_and_started",
        "raw_file": raw_path.name,
        "url": url,
        "title": title,
        "screenshot_file": screenshot_path.name if screenshot_path else None,
    }
