# Function documentation and type annotations

## What changed

Functions in the Python project were documented with:

- typed arguments;
- explicit return types;
- short docstrings describing what each function does.

The Firefox extension functions received equivalent JSDoc documentation for arguments and return behavior.

## Why

The codebase now contains enough modules that understanding a function's contract should not require opening its callers first.

The documentation also makes maintenance and future refactoring easier.

## Result

The main implementation remains unchanged while its public/internal function contracts are clearer for future development.
