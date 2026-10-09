# Scrapper Ollama

A local browser-assisted automation and task-processing framework built around Firefox, Python, Native Messaging, Ollama, and Docker. It captures the active page, builds a concise representation of its visible task, optionally processes supporting files and visual evidence, and validates the resulting answer before presenting it.

> **Project status:** Experimental software. Review configuration, security assumptions, and limitations before using it with sensitive data or publishing a deployment.

## Features

- User-triggered capture of the active Firefox tab without intentionally reloading or navigating away from it.
- DOM and screenshot capture through a Firefox WebExtension and Native Messaging host.
- Configurable HTML window extraction and deterministic task classification (`basic`, `file_task`, or `sandbox`).
- Deterministic Python processing for mechanical operations, with Ollama for interpretation and reasoning when needed.
- Optional screenshot and downloaded-image OCR through Tesseract, plus optional vision-model analysis.
- A quarantined file-processing pipeline with hashing, metadata, byte-based type detection, archive/member inspection, evidence collection, and explicit processing states.
- Docker isolation for untrusted file parsing, with network disabled and resource restrictions.
- Structured answer normalization, multiple-choice validation, dependency diagnostics, and machine-readable reports.
- Windows setup and Native Messaging registration scripts.

## Architecture overview

```text
Firefox WebExtension
        |
        v
Native Messaging host
        |
        v
Python pipeline
  |-- Capture metadata, DOM, screenshot
  |-- Clean/extract the relevant task window
  |-- Classify the task and discover supporting downloads
  |-- Download and quarantine untrusted bytes
  |-- Docker inspection, type/security validation, and safe extraction
  |-- OCR / optional vision analysis
  |-- Build structured evidence and a filtered model context
  |-- Deterministic processing and optional Ollama reasoning
  |-- Validate and normalize the answer
  `-- Record the result and apply cleanup policy
```

The file-inspection container is a security boundary, not a guarantee that every file is safe. The application should not open untrusted downloads in arbitrary desktop applications.

## Requirements

The primary setup path targets Windows and expects:

- Python 3.13;
- Firefox;
- Docker Desktop with a working daemon;
- Ollama and the models configured in `config/model_settings.json`;
- Tesseract OCR and the requested language data when OCR is enabled.

`setup.bat` is the main installation entry point. It verifies dependencies, installs missing components according to the setup policy, checks configured models and the quarantine image, and registers the Native Messaging host. It is designed to preserve an existing Ollama installation rather than reinstalling it simply because setup is rerun.

After setup, load `firefox_extension/manifest.json` through Firefox's temporary add-on workflow for development, or package/sign the extension according to the deployment method you choose. Restart Firefox after registering Native Messaging.

## Configuration

Key configuration files:

- `config/model_settings.json` — Ollama models and model-related settings.
- `config/vision_settings.json` — screenshot OCR and optional vision behavior.
- `config/window_rules.json` — CSS selectors and heuristic weights used to locate the relevant task window.
- `config/download_rules.json` — accepted file formats, download policy, parser/resource limits, OCR settings, antimalware mode, and cleanup threshold.
- `config/dependencies.json` — dependency setup policy.

The cross-origin download allow-list is empty by default. Add only hosts you explicitly trust to `download_allowed_hosts`; do not copy an example hostname into a real deployment without reviewing its download and redirect behavior.

Window selectors are examples and starting points. Different websites use different DOM structures, so adapt `config/window_rules.json` for your target pages and test the extraction output before relying on it.

## Data and privacy

Runtime files are created beneath `data/`, including captures, screenshots, evidence, reports, temporary download artifacts, and answer results. These paths are ignored by Git to reduce the chance of accidentally publishing captured pages or local diagnostic data. Inspect the repository and your local data before committing anything.

Page content, screenshots, extracted text, and downloaded files may contain private information. Model prompts and logs should be reviewed before sharing; do not commit credentials, captured user pages, local reports, or generated Native Messaging registration files.

## Development and tests

Run tests from the repository root:

```powershell
python -m pytest -q
```

Some integration tests require Docker or external services. If a required service is unavailable, run the service-independent tests separately and record the skipped or failing integration tests accurately.

Useful development areas:

- `app/` — capture orchestration, page processing, task classification, evidence, answering, and downloads.
- `firefox_extension/` — browser-side capture integration.
- `native_messaging/` — host implementation and installer.
- `quarantine/` — isolated file-inspection worker and Docker image definition.
- `config/` — configurable behavior and policy.
- `tests/` — unit, regression, and security-focused tests.
- `information/INFORMATION.md` — consolidated architecture and troubleshooting reference.
- `CHANGES/` — change history.

## Security notes

- Treat all downloaded bytes and archive members as untrusted until they pass the configured validation policy.
- Type detection uses the file's contents and structure; extensions and MIME types are consistency signals, not proof of safety.
- Archive processing must obey configured size, member-count, recursion, and time limits.
- Docker isolation reduces risk but does not eliminate parser vulnerabilities or host/runtime risks.
- Keep cross-origin download permissions narrow and review redirect policy.
- `ACCEPTED`, `REJECTED`, `UNSUPPORTED`, `INCONCLUSIVE`, and `ERROR` represent different outcomes; do not interpret acceptance as a mathematical safety guarantee.

## License

No license has been selected in this repository yet. Choose and add an appropriate license before publishing if you want to grant others permission to use, modify, and redistribute the code.
