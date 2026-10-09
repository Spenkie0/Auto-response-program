from __future__ import annotations

import re
from typing import Any


_CHOICE_NUMBER_PATTERNS = (
    re.compile(r"^\s*(\d+)\s*[.)]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:choice|option|answer|response|réponse|choix)\s*#?\s*(\d+)\s*[.:)]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(\d+)\s*[-:–—]\s*.+$", re.IGNORECASE),
)


def _clean(value: object) -> str:
    """Return a compact normalized representation of a value."""
    return " ".join(str(value or "").split()).strip()


def _extract_choice_number(answer: object) -> int | None:
    """Extract a 1-based choice number from common model answer formats."""
    text = _clean(answer)
    if not text:
        return None
    for pattern in _CHOICE_NUMBER_PATTERNS:
        match = pattern.match(text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def resolve_choice_answer(answer: object, choices: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve numeric choice answers to the exact visible choice text.

    The model is encouraged to return the visible choice text, but this deterministic
    layer also converts values such as ``3`` or ``choice 3`` into the corresponding
    choice sentence. If a numeric answer cannot be resolved, the result is marked
    invalid instead of passing an unexplained integer onward.
    """
    original = _clean(answer)
    if not choices or not original:
        return {"status": "unchanged", "answer": original}

    normalized_choices = []
    for index, choice in enumerate(choices, start=1):
        text = _clean(choice.get("text"))
        value = _clean(choice.get("value"))
        if text:
            normalized_choices.append({
                "index": index,
                "text": text,
                "value": value,
            })

    # An exact visible choice text is already in the desired form.
    lowered = original.casefold()
    for choice in normalized_choices:
        if choice["text"].casefold() == lowered:
            return {
                "status": "unchanged",
                "answer": choice["text"],
            }

    choice_number = _extract_choice_number(original)
    if choice_number is None:
        return {"status": "unchanged", "answer": original}

    # Prefer an explicit HTML value such as value="3" when present.
    for choice in normalized_choices:
        if choice["value"] == str(choice_number):
            return {
                "status": "resolved",
                "answer": choice["text"],
                "source": "choice_value",
                "choice_number": choice["index"],
                "original_answer": original,
            }

    # Fall back to the visible one-based choice order.
    for choice in normalized_choices:
        if choice["index"] == choice_number:
            return {
                "status": "resolved",
                "answer": choice["text"],
                "source": "choice_index",
                "choice_number": choice["index"],
                "original_answer": original,
            }

    return {
        "status": "invalid",
        "answer": None,
        "reason": f"The answer model returned choice {choice_number}, but no matching visible choice exists.",
        "original_answer": original,
    }
