# Change 29 — Duplicate cleanup and history normalization

## What changed

- Removed the unused legacy `app/downloads/service.py` module, which was no longer referenced and imported classes/functions that do not exist in the current download-security implementation.
- Renumbered the `CHANGES/` notes so every change has a unique sequential number; the previous history contained duplicate `12-` and `13-` prefixes.
- Checked non-generated source files for identical content. No duplicate implementation files remain; the only identical source hashes are intentionally empty `__init__.py` files.
- Identical captured HTML/Clean HTML/screenshots remain under `data/` because they are runtime evidence/fixtures, not duplicate source implementations.
