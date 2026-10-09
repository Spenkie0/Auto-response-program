from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class RestartError(RuntimeError):
    """Raised when the pipeline cannot launch its replacement process."""


def _project_root() -> Path:
    """Return the scraper project root directory."""
    return Path(__file__).resolve().parents[2]


def build_restart_command(resume_file: str | None = None) -> tuple[list[str], Path]:
    """Build an absolute-path command that resumes the current pipeline run."""
    root = _project_root()
    main_py = root / "main.py"
    if not main_py.exists():
        raise RestartError(f"Pipeline entry point not found: {main_py}")

    args = list(sys.argv[1:])
    if resume_file:
        if "--process-existing" not in args:
            args.append("--process-existing")
        if "--file" in args:
            index = args.index("--file")
            if index + 1 < len(args):
                args[index + 1] = resume_file
            else:
                args.append(resume_file)
        else:
            args.extend(["--file", resume_file])

    command = [str(Path(sys.executable).resolve()), str(main_py), *args]
    return command, root


def install_model(model: str) -> tuple[bool, str]:
    """Run ``ollama pull`` and report whether the requested model installed."""
    if not model or not model.strip():
        return False, "The requested Ollama model name is empty."
    if not shutil.which("ollama"):
        return False, "The 'ollama' command was not found in PATH."

    model = model.strip()
    print(f"[OLLAMA INSTALL] Installing model: {model}", flush=True)
    print("[OLLAMA INSTALL] Running: ollama pull " + model, flush=True)
    try:
        completed = subprocess.run(
            ["ollama", "pull", model],
            check=False,
        )
    except OSError as exc:
        return False, f"Could not start ollama: {exc}"

    if completed.returncode != 0:
        return False, f"ollama pull exited with code {completed.returncode}."
    return True, f"Model '{model}' installed successfully."


def restart_program(resume_file: str | None = None) -> None:
    """Start a replacement pipeline process in the current console and return."""
    command, root = build_restart_command(resume_file=resume_file)
    print("[RESTART] Starting the resumed scraper process...", flush=True)
    try:
        # No CREATE_NEW_CONSOLE is used here: the replacement process inherits
        # the existing persistent cmd /K console created by the Firefox trigger.
        subprocess.Popen(
            command,
            cwd=str(root),
            close_fds=False,
        )
    except OSError as exc:
        raise RestartError(f"Could not start the resumed scraper process: {exc}") from exc


def prompt_install_and_restart(model: str, stage: str, resume_file: str | None = None) -> bool:
    """Offer model installation, resume the same raw file, or stop cleanly."""
    print()
    print(f"[MODEL MISSING] The {stage} requires Ollama model '{model}'.")
    print("The raw/clean page has been kept; nothing will be discarded.")
    answer = input("Install this model now with 'ollama pull'? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        print("[MODEL INSTALL] Declined. The program will stop.")
        return False

    ok, message = install_model(model)
    print(f"[MODEL INSTALL] {message}", flush=True)
    if not ok:
        print("[MODEL INSTALL] Installation failed. The program will stop.", flush=True)
        return False

    try:
        restart_program(resume_file=resume_file)
    except RestartError as exc:
        print(f"[RESTART FAILED] {exc}", flush=True)
        return False

    # The current process must not continue with the old Ollama client, which
    # still reflects the pre-install state. Exit only after the replacement has
    # been successfully started. The persistent Windows console remains open.
    print("[RESTART] Current pipeline process exiting; resumed process is now running.", flush=True)
    raise SystemExit(0)
