# Generic public-repository cleanup

## Changes

- Added a root README covering the architecture, Windows setup, configuration, security boundaries, data handling, and development tests.
- Replaced target-specific sample pages, hostnames, labels, filenames, and DOM class names with neutral examples.
- Made task-window selectors generic and configurable, with deterministic heuristic fallback for pages using different markup.
- Changed embedded-sandbox detection to use generic sandbox/simulator semantics instead of fixed site-specific CSS classes.
- Cleared the default cross-origin download host allow-list; deployment-specific hosts must be explicitly configured.
- Removed generated Native Messaging files containing machine-specific absolute paths. The installer generates these files on the target machine.
- Added ignore patterns for runtime captures, screenshots, OCR data, reports, answer output, and generated Native Messaging registration artifacts.

## Validation

The source and documentation tree was scanned for target-specific names/URLs and machine-specific absolute paths. Regression tests are run as part of this cleanup; environment-dependent Docker integration results are reported separately.
