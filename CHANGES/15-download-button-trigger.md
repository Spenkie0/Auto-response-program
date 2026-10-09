# Change: Download Button Trigger

## What changed

The Firefox extension now recognizes an obvious visible `Télécharger` / `Download` button that does not expose a direct URL. Before sending the capture to Python, it can click a unique matching button and wait for Firefox's download manager to report the completed file.

The completed local download path is sent to the native Python host and enters the existing quarantine, byte-signature validation, anti-malware, archive inspection, and safe text-extraction workflow.

## Why

Some pages do not expose a downloadable URL in the captured DOM. They provide a JavaScript button instead. The Python URL detector therefore had nothing to download even though the page clearly offered a file.

This change keeps the existing direct-URL path for ordinary download links and adds a controlled browser-side path for the button case.

## Behavior

- Unique obvious download button: click it and wait for the Firefox download.
- Multiple obvious download buttons: do not guess; report an error.
- No download button: keep the existing direct-URL detection workflow.
- The downloaded file is still treated as untrusted input and never opened as a desktop application.
