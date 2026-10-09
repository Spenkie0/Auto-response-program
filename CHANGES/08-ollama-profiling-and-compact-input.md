# Ollama profiling and compact input

## What changed

The answer stage gained profiling information for local Ollama inference, including timing/token data available from the Ollama response.

The answer model can also receive a compact semantic representation of the cleaned window instead of the complete HTML.

The project keeps the full cleaned HTML available for debugging and supports using the full representation when needed.

## Why

Local LLM inference was making the computer work noticeably harder and could take time to answer.

A large HTML document contains much more information than the model needs to answer a question. Sending a compact representation reduces context size and provides a clearer debugging signal.

## Result

The logs can show where time is being spent, while the model can operate on a much smaller input for appropriate page types.
