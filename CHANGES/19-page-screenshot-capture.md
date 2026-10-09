# Change 17 — Page Screenshot Capture

## What changed

The Firefox extension now captures a PNG screenshot of the visible active tab at the same moment it captures the live DOM.

The screenshot is saved locally under:

```text
data/screenshots/<capture-timestamp>.png
```

The screenshot is linked to the corresponding raw HTML capture by the shared timestamp.

The Ollama layer also supports attaching that screenshot to a vision-capable model through the optional `vision_model` setting.

## Why

Some page information is easier to understand visually than from HTML alone, including rendered labels, layout, canvas content, visual controls, and information whose HTML representation is incomplete or ambiguous.

The screenshot is therefore a complementary source, not a replacement for the structured HTML task representation.

## Current behavior

The default configuration leaves `vision_model` empty, so the screenshot is captured and stored but is not sent to the text-only answer model. To use screenshots with Ollama, configure a model that accepts image inputs:

```json
{
  "vision_model": "your-vision-model"
}
```

The `--no-screenshot` option disables screenshot use for a run, while the actual browser capture remains a user-triggered `activeTab` action.
