# Change 28 — Image type detection and extension aliases

## What changed

- Added explicit byte-signature detection for JPEG/JPG, PNG, GIF, BMP, TIFF, WEBP, ICO, HEIF, and AVIF images.
- JPEG is detected from its bytes as canonical `.jpg`; `.jpeg` is treated as an explicit extension alias through configuration.
- TIFF `.tif`/`.tiff` is handled as an extension family through configuration.
- Common binary formats that already had signatures now also expose canonical extensions instead of `None` (for example `.pdf`, `.rar`, `.7z`, `.gz`, `.exe`, `.elf`, `.ole`).
- Generic ZIP files now have canonical `.zip` detection when they are not recognized as DOCX/ODT containers.
- Unknown binary bytes can no longer be accepted merely because the filename ends in an allowed extension.
- Recognized image files can pass safe ingestion as binary evidence even when no textual extraction exists; their stable evidence record marks the image as present.

## Why

A `.jpeg` filename and a `.jpg` filename are two names for the same JPEG family. The bytes, not the filename, determine the actual type. The policy layer therefore supports configured aliases while still requiring successful byte-level detection before a binary file can be accepted.

## Configuration

`config/download_rules.json` now contains image extensions and explicit aliases such as:

```json
"accepted_extensions": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico"],
"extension_aliases": {
  ".jpg": [".jpeg"],
  ".tif": [".tiff"]
}
```
