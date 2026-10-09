# Change 25 - Automatic dependency bootstrap

Added a Windows dependency/bootstrap layer so the project can prepare its own runtime instead of assuming every prerequisite is already installed.

## Managed components

- Python runtime floor
- Host Python libraries
- Firefox
- Docker Desktop + Docker daemon readiness
- Tesseract OCR + requested language data
- Ollama
- Configured Ollama models
- Reusable Docker quarantine image
- Firefox Native Messaging registration

## Runtime behavior

The normal pipeline can propose Tesseract installation when a screenshot is available, OCR is enabled, and Tesseract is missing. Declining leaves OCR unavailable without fabricating evidence.

`setup.bat` is the bootstrap path that can install Python before the Python program itself runs. `scripts/setup_windows.py` performs the full dependency verification after a Python runtime exists.

WinGet remains the Windows application installer backend. The project deliberately does not silently download an arbitrary replacement for missing WinGet/App Installer.
