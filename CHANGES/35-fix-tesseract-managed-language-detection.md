# Fix Tesseract managed-language verification

Fixed language parsing when `tesseract --list-langs --tessdata-dir` reports managed entries as `tessdata/eng` or `tessdata\\fra`. The setup and runtime OCR paths now normalize these entries to canonical language codes, preventing false `eng`/`fra` missing failures.
