from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.dependency_installer import collect_dependency_diagnostics, ensure_all_dependencies  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Install and verify every runtime dependency for Scrapper Ollama on Windows.")
    parser.add_argument("--yes", action="store_true", help="Accept all dependency installation prompts.")
    parser.add_argument("--no-models", action="store_true", help="Install applications/libraries but do not pull configured Ollama models.")
    args = parser.parse_args()

    print("=" * 68)
    print(" SCRAPPER OLLAMA - WINDOWS DEPENDENCY SETUP")
    print("=" * 68)
    print("[SETUP] Checking Python, Python libraries, Firefox, Docker Desktop, Tesseract and Ollama...")
    print()

    statuses = ensure_all_dependencies(ROOT, prompt=not args.yes, include_models=not args.no_models)
    python_status = next((status for status in statuses if status.key == "python"), None)
    if python_status and python_status.installed and "relaunch" in python_status.detail.lower():
        import subprocess
        print("[SETUP] Python 3.13 is installed. Relaunching the full setup with Python 3.13...")
        command = ["py", "-3.13", str(Path(__file__).resolve()), *(["--yes"] if args.yes else []), *(["--no-models"] if args.no_models else [])]
        completed = subprocess.run(command, check=False)
        return completed.returncode

    failed = [status for status in statuses if not status.installed]
    for status in statuses:
        state = "OK" if status.installed else "MISSING"
        print(f"[SETUP] {state:<7} {status.label}: {status.detail}")

    report = collect_dependency_diagnostics(ROOT)
    report_path = ROOT / "data" / "dependency_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SETUP] Dependency health report: {report_path}")

    if failed:
        print()
        print("[SETUP] Some dependencies are still missing. The program will not pretend setup succeeded.")
        return 1

    print()
    print("[SETUP] All configured dependencies are installed and verified.")
    print("[SETUP] Native Firefox messaging can be registered with:")
    print("        python native_messaging/install_native_host.py")
    print("[SETUP] The Firefox extension still needs to be loaded in Firefox according to Firefox's add-on workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
