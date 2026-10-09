# Change 22 — Unified File Evidence Architecture

## What changed

The downloaded-file pipeline now has a stable evidence interface that sits between secure validation/extraction and the thinking model.

After a file passes validation inside the quarantine backend, the pipeline builds a fixed JSON structure containing:

- file information;
- metadata;
- structure;
- text;
- tables;
- images;
- OCR;
- visual analysis;
- embedded content;
- security findings.

All sections exist in every evidence object. A genuinely absent category is represented by `status: "non-existent"` with null value fields rather than being omitted.

The evidence object is persisted separately under `data/downloads/evidence/` and is included in the answer model's task-first representation.

JPEG/PNG/GIF/BMP/TIFF/WEBP byte signatures were also added so image files are classified as image types instead of falling through to `unknown-binary`.

## Architectural rule

The order is now explicit:

```text
untrusted bytes
  -> quarantine
  -> hash / actual type / structure / security validation
  -> evidence extraction stages
  -> fixed evidence JSON
  -> thinking model
```

No evidence extraction stage is allowed to run before validation succeeds.

## Scope

Current supported text/document parsers continue to provide their existing text extraction. The stable evidence contract now provides the place for image extraction, OCR, visual analysis, richer metadata, tables, and embedded-content analyzers as those stages are added.

## File layout review

The download subsystem now has one clear responsibility per module:

```text
detector.py         -> what is this file?
rules.py            -> are we configured to allow it?
security.py         -> may processing continue?
docker_quarantine.py-> where is untrusted parsing performed?
extract.py          -> how do we safely extract supported content?
evidence.py         -> what facts do we expose to the model?
workflow.py         -> how is the whole lifecycle coordinated?
```

This keeps policy, detection, isolation, extraction, evidence generation, and lifecycle cleanup separate instead of allowing a single module to silently make multiple classes of security decision.
