# 33 - Tesseract language-data installation without Program Files writes

## Fixed

- Prevented setup from trying to write missing `.traineddata` files directly into the system Tesseract `tessdata` directory under `Program Files`.
- Added a project-managed user-writable OCR cache at `data/tesseract/tessdata`.
- Existing system language models are copied into that cache only when a cache is needed; the system Tesseract installation is never modified.
- Missing configured language models are downloaded from the official Tesseract `tessdata` repository and verified by running Tesseract with the managed data root.
- Screenshot OCR now explicitly uses the managed cache with `--tessdata-dir` when present.
- The live pipeline now verifies Tesseract language data even when `tesseract.exe` is already installed; previously this repair path only ran when the executable itself was missing.
- Dependency diagnostics now report managed Tesseract language data separately.

## Security / reliability

- Downloads are staged into temporary files and atomically renamed only after the transfer completes.
- Setup never requires elevation solely to install OCR language data.

## Verification

- Added regression tests for the project-managed language-data cache and explicit `--tessdata-dir` OCR invocation.
- Full project regression suite must be run before release packaging.
