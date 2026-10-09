# Screenshot OCR, visual evidence, and detailed CLI observability

## What changed?

The screenshot captured with each Firefox DOM snapshot is now treated as a first-class evidence source. The processing pipeline can:

- detect screenshot presence and dimensions;
- run OCR through the configurable Tesseract executable;
- optionally send the screenshot to a configured vision-capable Ollama model for visual observations;
- persist a stable screenshot evidence JSON object;
- attach that evidence to the final answer-model request while keeping the answer model as the reasoning component;
- expose the processing stages through a clearer CLI view.

The new configuration file is `config/vision_settings.json`. The existing `config/model_settings.json` remains responsible for model names.

## Why?

Some page information is visible in pixels but is not represented reliably in the DOM, especially screenshots, canvas content, visual labels, charts, and complex simulator interfaces. OCR and visual analysis supply that missing evidence without making the vision model responsible for solving the task.

## Architecture

```text
Firefox capture
      ↓
PNG screenshot
      ↓
Screenshot evidence
   ├── OCR
   └── visual analysis (optional)
      ↓
thinking model
      ↓
answer
```

The screenshot/visual stages are evidence producers. The answer model remains responsible for interpreting the page task together with screenshot evidence and any validated file evidence.

## CLI

The visible console now groups the run into capture, task classification, screenshot, download, answer-input, Ollama, and final-result sections. Ollama timing/profile JSON remains available under `data/debug/ollama/`.

## Tests

This change adds dedicated tests for the screenshot evidence schema, OCR execution/failure handling, visual-model handoff, persistence, and inclusion in the answer-model payload. The complete suite passed after the change.
