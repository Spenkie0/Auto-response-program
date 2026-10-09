# Project changes

This folder is the project's architectural and functional change history.

Open [[00-change-history]] for the complete chronological index.

## Recent changes

- [[23-config-driven-download-acceptance]] — configuration-driven download policy.
- [[24-file-evidence-architecture]] — stable file-evidence JSON.
- [[25-screenshot-ocr-and-cli-observability]] — screenshot OCR and visual evidence.
- [[26-answer-output-deduplication-and-concise-model-response]] — single final answer output.
- [[27-automatic-dependency-bootstrap]] — Windows dependency bootstrap.
- [[28-image-type-detection-and-extension-aliases]] — JPEG/JPG aliases and broader image detection.
- [[29-duplicate-cleanup]] — stale duplicate implementation cleanup and unique change numbering.
- [[30-secure-file-processing-pipeline]] — recursive untrusted-file inspection, hardened download/Docker boundaries, structured pipeline state, provenance, AI evidence filtering, dependency diagnostics, and expanded tests.
- [[31-setup-preserves-existing-ollama]] — setup no longer replaces an existing Ollama installation.
- [[32-dependency-diagnostics-docker-image-check]] — restored Docker image diagnostics.
- [[33-tesseract-language-cache]] — user-writable Tesseract language-data cache and explicit OCR data-root handling.
- [[34-fix-tesseract-diagnostics-variable-regression]] — fixed managed Tesseract language verification during dependency diagnostics.

- [[35-fix-tesseract-managed-language-detection]] — normalizes language names reported through a managed OCR data directory.
- [[36-generic-public-repository-cleanup]] — adds the root README, genericizes examples/configuration, and removes machine-specific generated files.
