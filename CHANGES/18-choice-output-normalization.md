# 16 - Choice answers are returned as visible text

## What changed

The answer stage now recognizes multiple-choice/selection questions and requires the final answer to be the visible text of the selected choice.

The pipeline has two protections:

1. The answer prompt tells the Ollama model not to return the numeric choice index/value.
2. Python post-processes the model response and converts common forms such as `3`, `choice 3`, `option 3`, or `3 - Enable two-factor authentication` into the corresponding visible choice text.

## Why

HTML controls commonly expose numeric values such as `value="3"`, while the user-facing answer is the sentence next to the control. Returning `3` is therefore technically related to the DOM but not the answer the user needs.

The final terminal output and saved answer JSON now contain the visible choice text, while the original model answer is retained in JSON as `model_answer` when a conversion occurred.

## Safety behavior

If the model returns a numeric choice that cannot be matched to any visible choice, the pipeline treats it as an invalid model response instead of guessing which choice it meant.
