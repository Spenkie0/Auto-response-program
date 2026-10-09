# Download-detection false-positive fix

The download detector previously treated generic prose mentioning files or documents as evidence that the page required a download. A file upload control (`input[type=file]`) was also incorrectly classified as a download signal.

The classifier now distinguishes text that discusses files from an actual download action or candidate. Generic references to spreadsheet/document formats remain ordinary task context unless a real download signal is present.

## Changes

- Removed generic file/document wording from download-only signals.
- Limited control-text detection to actual interactive controls (`a`, `button`, or `[role=button]`).
- Kept explicit download wording as a valid signal.
- Removed `input[type=file]` from download detection; it is an upload/local-file control, not a download.
- Kept local file controls as a separate `FILE_TASK` classification signal so future file-interaction support can handle them without pretending a download occurred.
- Added regressions for generic file/document mentions, upload inputs, explicit download prose, and URL-less download buttons.
