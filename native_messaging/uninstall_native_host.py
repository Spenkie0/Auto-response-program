from __future__ import annotations

import os
import sys

HOST_NAME = "scrapper_ollama"
REGISTRY_ROOT = "Software\\Mozilla\\NativeMessagingHosts\\" + HOST_NAME


def uninstall() -> None:
    """Remove the scraper native-host registration from the current user registry."""
    if os.name != "nt":
        raise RuntimeError("This uninstaller currently targets Windows Firefox.")
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_ROOT)
    except FileNotFoundError:
        print("[NATIVE HOST] Registry entry was already absent.")
        return
    print("[NATIVE HOST] Uninstalled from the current user's Firefox native messaging registry.")


if __name__ == "__main__":
    try:
        uninstall()
    except Exception as exc:
        print(f"[NATIVE HOST UNINSTALL FAILED] {exc}", file=sys.stderr)
        raise SystemExit(1)
