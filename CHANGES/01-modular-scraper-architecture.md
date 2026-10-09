# Modular scraper architecture

## What changed

The original large scraper was split into separate responsibilities instead of keeping capture, parsing, storage, CLI handling, and browser interaction in one large file.

The project evolved toward modules for:

- configuration and CLI handling;
- Firefox capture;
- HTML parsing/window extraction;
- Ollama communication;
- error handling;
- storage;
- debugging/profiling;
- tests.

## Why

The original scraper had grown large and mixed many unrelated responsibilities. A modular structure makes it easier to:

- change the Firefox capture mechanism without rewriting the processing pipeline;
- test individual parts independently;
- replace or improve the HTML extraction logic;
- change Ollama models and prompts independently;
- handle errors without putting recovery logic into every component.

## Result

The browser-facing layer and the data-processing layer became independent, which made the later WebExtension, native-messaging, and Ollama stages possible without rebuilding the whole application.
