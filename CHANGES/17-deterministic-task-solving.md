# 15 - Deterministic task solving before Ollama

## What changed

A new `app/answering/` layer now attempts to solve small, exact text-manipulation tasks before the main Ollama answer model is called.

Current deterministic operations include:

- replacing a requested word/string and counting characters;
- replacing a requested word/string and counting words;
- counting characters directly;
- counting words directly.

The solver extracts the task wording from the cleaned question representation and, when a supported operation is unambiguous, works directly on the safely extracted document text.

## Why

The main 8B model was being asked to perform operations that Python can perform exactly. This can waste local compute and can also produce a confident but incorrect result for mechanical operations such as character counting.

Deterministic tasks now receive a confidence of `1.0` from the solver and bypass Ollama entirely.

Unsupported or genuinely reasoning-heavy tasks continue to the configured Ollama answer model.

## Terminal output

When an answer is produced, the final user-facing line printed by the processing console is only the answer text. Reasoning, confidence, model metadata, and diagnostics remain in the JSON/debug data rather than being printed as the final answer.
