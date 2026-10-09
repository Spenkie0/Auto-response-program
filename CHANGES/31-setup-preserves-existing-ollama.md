# 31 - Setup preserves existing Ollama installations

The Windows bootstrap no longer invokes a direct `winget install` for Ollama (or the other configured external applications). `scripts/setup_windows.py` is now the single dependency-installation authority.

When an application is already detected, setup reports it and leaves the existing installation untouched. For Ollama, setup also reports the detected CLI/server version without reinstalling it.

WinGet installations requested by the dependency manager use `--no-upgrade` as an additional guard against replacing or upgrading an already installed package.

This prevents an existing Ollama installation from being unexpectedly replaced by a different/older package source during `setup.bat`. The missing-application path can still install Ollama using the configured installer policy.

Regression verification after this change: `pytest -q` → **181 passed, 43 subtests passed**.
