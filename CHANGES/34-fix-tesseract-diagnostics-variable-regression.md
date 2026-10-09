# Fix Tesseract diagnostics variable regression

Fixed the Windows dependency diagnostics path so managed Tesseract language verification uses the resolved Tesseract executable.

The previous implementation referenced an undefined `tesseract` variable after the user-writable language-data cache was added, causing setup to abort with `NameError` when a managed tessdata directory existed.

## Verification

- Added regression coverage for managed Tesseract language diagnostics.
- Full test suite rerun after the fix.
