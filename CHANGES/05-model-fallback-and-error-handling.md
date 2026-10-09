# Model fallback and error handling

## What changed

A two-level model role was introduced:

1. a smaller model may verify ambiguous HTML-window extraction;
2. a stronger model answers questions from the cleaned window.

The project also gained explicit error handling for conditions such as:

- Ollama unavailable;
- requested model missing;
- invalid or unusable model response;
- extraction failures;
- file/storage failures;
- unexpected runtime errors.

## Why

HTML structure is often easier to solve deterministically than with an LLM. The LLM is therefore used as a fallback where the deterministic extractor is uncertain.

Answer generation is a separate concern. The stronger model receives the cleaned page and decides whether it is sufficiently certain to provide an answer.

Technical failures must not be confused with an answer that the model cannot determine.

## Result

Extraction uncertainty can be routed to `review/`, while technical failures are routed to `errors/`. An uncertain answer is represented separately as `answer_not_found`.
