# Data folder and interactive model installer

## What changed

Runtime/generated content was moved under a dedicated `data/` directory:

```text
data/
├── raw_pages/
├── clean_window_pages/
├── answer/
├── answer_not_found/
├── review/
├── errors/
└── debug/
```

A missing Ollama model no longer causes an opaque failure. The terminal can offer to install the missing model.

The behavior is:

```text
model missing
   ↓
ask whether to install
   ├── No  → stop
   └── Yes → install
              ↓
           restart
```

The restart resumes the file that triggered the missing-model error rather than requiring a new browser capture.

## Why

Keeping runtime data separate from source code and configuration makes the project easier to manage and keeps generated communication data in one place.

The interactive installer avoids silently downloading software or models without the user's approval.

## Result

Model installation is explicit and recoverable, and the runtime data has a clear home.
