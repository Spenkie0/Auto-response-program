from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOST_NAME = "scrapper_ollama"
EXTENSION_ID = "active-firefox-scraper@example.local"
REGISTRY_ROOT = "Software\\Mozilla\\NativeMessagingHosts\\" + HOST_NAME


def build_launcher(project_root: Path) -> Path:
    """Create the Windows batch launcher registered as the Firefox native host."""
    native_dir = project_root / "native_messaging"
    launcher = native_dir / "scrapper_ollama_host.bat"
    python_exe = Path(sys.executable).resolve()
    host_py = (native_dir / "native_host.py").resolve()
    launcher.write_text(
        "@echo off\n"
        f'"{python_exe}" -u "{host_py}"\n',
        encoding="utf-8",
        newline="\r\n",
    )
    return launcher


def install() -> Path:
    """Register the native host manifest in the current user Firefox registry key."""
    if os.name != "nt":
        raise RuntimeError("This installer currently targets Windows Firefox.")

    import winreg

    project_root = Path(__file__).resolve().parent.parent
    native_dir = project_root / "native_messaging"
    native_dir.mkdir(parents=True, exist_ok=True)
    launcher = build_launcher(project_root)
    manifest_path = native_dir / f"{HOST_NAME}.json"
    manifest = {
        "name": HOST_NAME,
        "description": "Native host that receives a Firefox DOM capture and starts the local pipeline.",
        "path": str(launcher),
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_ROOT) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))

    print("[NATIVE HOST] Installed successfully.")
    print(f"[NATIVE HOST] Manifest: {manifest_path}")
    print(f"[NATIVE HOST] Registry: HKCU\\{REGISTRY_ROOT}")
    print("[NATIVE HOST] Restart Firefox after installation if the extension is already open.")
    return manifest_path


if __name__ == "__main__":
    try:
        install()
    except Exception as exc:
        print(f"[NATIVE HOST INSTALL FAILED] {exc}", file=sys.stderr)
        raise SystemExit(1)
