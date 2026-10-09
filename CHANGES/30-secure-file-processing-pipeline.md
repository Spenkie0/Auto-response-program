# Change 30 — Secure file-processing pipeline and observable stages

## What changed

The download/file-analysis path was completed as a segmented security pipeline while preserving the existing Firefox → Native Messaging → Python → Ollama architecture.

### Download and network boundary

- Added explicit URL scheme, URL-length, redirect, host/origin, response-header, response-size, and DNS destination policy.
- Added redirect-chain provenance.
- Added DNS pinning for the validated request/redirect chain.
- Kept URL extension optional; no extension means `null` and no URL/type comparison.
- URL/type mismatch is a rejection signal, not a type-detection mechanism.

### Quarantine and Docker

- Removed the production host-side/local parsing backend.
- Kept the disposable Docker worker as the only production parser boundary.
- Preserved network isolation, read-only root filesystem, no-new-privileges, dropped capabilities, non-root UID, CPU/memory/PID/time limits, no host mounts, and deterministic JSON output.
- Added image-ID/config/schema provenance and a rootless-support observation.
- Host and worker independently verify SHA-256 before accepting the result.

### Recursive archive security

- ZIP/DOCX/ODT containers are inspected member-by-member.
- Added per-member SHA-256, detected type, canonical type, MIME, parent archive hash, depth, findings, and status.
- Added bounded nested-archive recursion.
- Added Windows-aware archive-name checks, duplicate detection, compression-ratio checks, XML limits, member/total limits, and processing deadlines.
- Added active-content/VBA/ActiveX/script/executable checks.

### Image/document processing

- Added dedicated image validation, dimension/pixel bounds, metadata/selected EXIF evidence, safe re-encoding to PNG, and bounded OCR.
- Embedded document images use the same image path.
- Document relationships are inspected without fetching external resources.
- Optional vision reasoning consumes only Docker-produced sanitized PNG artifacts, never original untrusted image bytes.

### Evidence, AI context, and answer validation

- Added structured evidence lineage and stable safe artifact hashes.
- Added allow-list filtering so quarantine paths, debug data, and internal provenance are not exposed to the answer model.
- Added validation for optional model `choice_index` and deterministic reconciliation against visible choice text.
- Added `validated_confidence` and explicit confidence provenance for cleanup decisions.

### Pipeline/error architecture

- Added explicit top-level `PipelineRunState` and stage records.
- Added monotonic transition enforcement and first-failure preservation.
- Added centralized machine-readable error-code families.
- Added machine-readable dependency health reporting to `data/dependency_report.json`.

### Cleanup

- Cleanup continues to require strict `confidence > 0.90`.
- Rejected/error/inconclusive/security-failure artifacts remain retained according to policy.

### Tests and documentation

- Added negative/security tests for nested archives, per-member provenance, URL consistency, Windows archive names, external relationships, image sanitization, AI evidence filtering, state transitions, and structured answer choice validation.
- Updated consolidated architecture/security/testing documentation.
- Removed obsolete `app/downloads/ingest.py`, the old localhost capture bridge under `app/bridge/`, and the pre-Native-Messaging countdown helper under `app/browser/`.

## Why

The previous implementation had a strong Docker boundary but still left important security policy and provenance decisions implicit or incomplete. This change turns those decisions into explicit, testable contracts while keeping the original project architecture intact.

## Verification

`python -m pytest -q` → **179 passed, 43 subtests passed**.
