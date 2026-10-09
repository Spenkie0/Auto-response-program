from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    """Return the project directory containing main.py."""
    return Path(__file__).resolve().parent.parent


def run_pipeline(arguments: list[str]) -> int:
    """Run main.py with the supplied arguments and return its exit code."""
    root = project_root()
    main_py = root / "main.py"
    completed = subprocess.run(
        [sys.executable, "-u", str(main_py), *arguments],
        cwd=str(root),
        check=False,
    )
    return completed.returncode


def main() -> int:
    """Launch the pipeline and keep a Windows console open for diagnostics."""
    code = run_pipeline(sys.argv[1:])
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            input("\n[PIPELINE] Finished. Press Enter to close this console... ")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
