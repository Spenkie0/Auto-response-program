# Rule-based task-window extraction

## What changed

The page-window extractor supports configurable CSS selectors for locating a task container. The default configuration uses generic task-item, question, and response classes, then falls back to deterministic candidate scoring when no configured selector matches.

## Why

A stable structural selector is more predictable than asking a language model to rediscover a page boundary every time. A task container also provides a useful stopping point so unrelated sibling content, such as feedback or navigation, can be excluded from the cleaned representation.

## Configuration

Selectors are defined in `config/window_rules.json`. Adapt them to the markup used by the pages the application is intended to process. Keep the original capture under the runtime data directory and validate extraction results when changing rules.
