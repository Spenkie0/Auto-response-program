# 24 — Answer-output de-duplication and concise model responses

## Changes

- Confirmed that the production answer model has a single call site in `Pipeline._answer`; the apparent duplicate answer in the terminal came from printing `decision["answer"]` inside `[RESULT]` and printing it again as the final user-facing answer.
- Removed the answer text from the diagnostic `[RESULT]` block.
- Added `[FINAL ANSWER]` as the single user-facing answer output.
- Tightened the answer prompt to request only the main information that distinguishes the correct answer from alternatives.
- Multiple-selection answers must contain only the exact visible selected choice texts.
- Short factual answers should contain only the needed value or phrase.
- Reasoning answers may contain the conclusion plus at most one short supporting clause.
- The model `reason` field must be one short evidence-based sentence and must not repeat the answer.
- Added regression tests for the prompt and CLI de-duplication behavior.
