# Raw pages and clean-window pipeline

## What changed

The project stopped treating the captured page as the final input to the answer model.

The processing flow became:

```text
Firefox
  ↓
raw_pages/
  ↓
HTML window extraction
  ↓
clean_window_pages/
  ↓
answer model
```

The raw capture is kept as the original source material. The cleaned window is a derived representation of the part of the page that matters.

## Why

A full modern webpage can contain navigation, styles, scripts, tracking elements, buttons, dialogs, advertisements, and other unrelated markup.

Keeping a raw copy gives the project a stable source that can be reprocessed later when the extraction rules change.

The separate cleaned stage also means the answer model does not need to understand the entire page structure every time.

## Result

The system became reproducible: the same raw page can be processed again with newer extraction rules without having to revisit Firefox.
