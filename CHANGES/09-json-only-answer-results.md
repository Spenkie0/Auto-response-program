# JSON-only answer results

## What changed

The answer stage stopped creating three output artifacts (`json`, `html`, and `txt`).

It now creates exactly one result file per processed page:

```text
data/answer/<timestamp>.json
```

or:

```text
data/answer_not_found/<timestamp>.json
```

The original cleaned HTML remains in `data/clean_window_pages/` as the canonical source.

## Why

The clean HTML already exists in its own dedicated directory. Duplicating it into the answer directory added unnecessary copies.

The JSON result is enough to store the model decision, confidence, answer/reason, and processing metadata.

## Result

The answer directories contain only the decision/results produced by the answer stage, while the cleaned HTML remains centralized.
