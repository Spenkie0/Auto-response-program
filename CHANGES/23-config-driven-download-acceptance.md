# Change 21 — Config-driven download acceptance

The download-security policy now uses `config/download_rules.json` as the source of truth for `accepted_extensions`.

## What changed

- Removed hard-coded rejection of `unknown-binary`, PE, ELF, OLE, RAR, 7z, gzip, and PDF types from the extension-policy gate.
- Removed the hard-coded rejection of ZIP containers whose internal document kind is not recognized.
- Validation now checks whether the detected canonical extension **or** the original source extension is present in `accepted_extensions`.
- Safe parsing remains deliberately separate from the allow-list: a configured extension may be permitted by policy but still produce `UNSUPPORTED_SAFE_FORMAT` when no safe parser exists for the detected content.
- Text/CSV and DOCX/ODT parser selection is now driven by byte/structure detection, so explicitly configured custom extensions can still use the appropriate safe parser when their content is supported.
- Task classification no longer embeds the default accepted-extension list; when no list is supplied, it loads the project's `config/download_rules.json`.
- The fallback rules now contain an empty accepted-extension list, so a missing download-rules file fails closed instead of silently restoring an application-code allow-list.

## Security behavior

Removing the hard-coded format allow/reject list does **not** execute or open accepted files. Untrusted downloads still enter the configured quarantine backend, and the Docker worker still performs the detection and parsing inside the isolated container.

The distinction is now:

1. `accepted_extensions` controls **policy permission**.
2. byte/structure detection controls **how the file is interpreted**.
3. safe parser availability controls **whether text/data can actually be extracted**.
