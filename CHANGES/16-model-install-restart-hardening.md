# Model Installation and Restart Hardening

## What changed

The model-install path was hardened so a successful `ollama pull` does not leave the pipeline process in a stale state or make the visible Windows console disappear.

## Why

The running Python process has already observed the model as missing. Continuing in that process after installation could reuse stale state. The replacement process must start from the same raw capture with a fresh Ollama client.

## Behavior

1. The user accepts model installation.
2. `ollama pull <model>` completes successfully.
3. A replacement `main.py` process is started with the same CLI settings and the same `data/raw_pages` file.
4. The old process exits.
5. On Windows, the pipeline is hosted by `cmd /d /k`, so the console stays open while the resumed process runs and remains visible after completion.
6. If restarting fails, the current process reports `[RESTART FAILED]` and stops without losing the raw/clean page.

## Testing

The test suite includes installation success/decline/failure, restart-argument preservation, resume-file replacement, restart spawn failure, Windows persistent-console command construction, metadata safety, and repeated model-setting cases.
