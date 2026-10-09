from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..answer_representation import build_compact_representation


_REPLACEMENT_PATTERNS = (
    re.compile(
        r"(?:remplace(?:z|r)?|remplacer)\s+(?:le\s+mot\s+)?"
        r"(?P<old>[\"«“][^\"»”]+[\"»”]|[\wÀ-ÿ'-]+)\s+"
        r"(?:par|avec)\s+(?:le\s+mot\s+)?"
        r"(?P<new>[\"«“][^\"»”]+[\"»”]|[\wÀ-ÿ'-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"replace\s+(?:the\s+word\s+)?"
        r"(?P<old>[\"“][^\"”]+[\"”]|\S+)\s+"
        r"(?:with|by)\s+(?:the\s+word\s+)?"
        r"(?P<new>[\"“][^\"”]+[\"”]|\S+)",
        re.IGNORECASE,
    ),
)

_CHARACTER_PATTERNS = (
    re.compile(r"combien\s+de\s+caract(?:è|e)res", re.IGNORECASE),
    re.compile(r"nombre\s+de\s+caract(?:è|e)res", re.IGNORECASE),
    re.compile(r"compter\s+les\s+caract(?:è|e)res", re.IGNORECASE),
    re.compile(r"how\s+many\s+characters", re.IGNORECASE),
    re.compile(r"character\s+count", re.IGNORECASE),
)

_WORD_PATTERNS = (
    re.compile(r"combien\s+de\s+mots", re.IGNORECASE),
    re.compile(r"nombre\s+de\s+mots", re.IGNORECASE),
    re.compile(r"how\s+many\s+words", re.IGNORECASE),
    re.compile(r"word\s+count", re.IGNORECASE),
)

_EXCLUDE_SPACE_PATTERNS = (
    re.compile(r"sans\s+les\s+espaces", re.IGNORECASE),
    re.compile(r"sans\s+espaces", re.IGNORECASE),
    re.compile(r"excluding\s+spaces", re.IGNORECASE),
    re.compile(r"without\s+spaces", re.IGNORECASE),
)

_QUOTE_PAIRS = (("\"", "\""), ("'", "'"), ("«", "»"), ("“", "”"))


@dataclass(frozen=True)
class DeterministicAnswer:
    """Represent an answer produced entirely by a deterministic task solver."""

    answer: str
    solver: str
    operation: str
    confidence: float = 1.0
    details: dict[str, Any] | None = None

    def as_dict(self, *, filename: str, input_mode: str) -> dict[str, Any]:
        """Return the standardized answer decision consumed by the pipeline."""
        return {
            "status": "answer",
            "confidence": self.confidence,
            "answer": self.answer,
            "reason": f"Solved deterministically with {self.solver}.",
            "solver": self.solver,
            "operation": self.operation,
            "filename": filename,
            "input_mode": input_mode,
            "details": self.details or {},
        }


def _strip_quotes(value: str) -> str:
    """Remove surrounding matching quotation marks and sentence punctuation."""
    value = value.strip().strip(".,;:!?)]}")
    for opening, closing in _QUOTE_PAIRS:
        if value.startswith(opening) and value.endswith(closing) and len(value) >= 2:
            value = value[1:-1]
            break
    return value.strip()


def _find_replacement(task: str) -> tuple[str, str] | None:
    """Find a simple textual replacement instruction in the task wording."""
    for pattern in _REPLACEMENT_PATTERNS:
        match = pattern.search(task)
        if not match:
            continue
        old = _strip_quotes(match.group("old"))
        new = _strip_quotes(match.group("new"))
        if old and new and old != new:
            return old, new
    return None


def _contains_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    """Return whether any supplied regular-expression pattern matches the text."""
    return any(pattern.search(text) for pattern in patterns)


def _replace_all(text: str, old: str, new: str) -> tuple[str, int]:
    """Replace all literal occurrences and report how many replacements were made."""
    if re.fullmatch(r"[\wÀ-ÿ'-]+", old, flags=re.UNICODE):
        pattern = re.compile(rf"(?<![\wÀ-ÿ'-]){re.escape(old)}(?![\wÀ-ÿ'-])", re.IGNORECASE)
        replaced, count = pattern.subn(new, text)
        return replaced, count
    replaced, count = re.subn(re.escape(old), new, text)
    return replaced, count


def solve_deterministic_task(
    clean_html: str,
    *,
    filename: str,
    page_url: str | None = None,
    title: str | None = None,
    attachment_text: str | None = None,
    attachment_metadata: dict[str, object] | None = None,
    max_representation_chars: int = 40000,
) -> DeterministicAnswer | None:
    """Solve a small class of exact text tasks without calling an LLM.

    Supported operations currently include replacing a word/string and then
    counting characters, or counting words/characters in an attached document.
    Return None when the task is not unambiguously supported by these rules.
    """
    representation = build_compact_representation(
        clean_html,
        filename=filename,
        page_url=page_url,
        title=title,
        max_text_chars=max_representation_chars,
        attachment_text=attachment_text,
        attachment_metadata=attachment_metadata,
    )
    task = representation.get("task")
    if not isinstance(task, dict):
        return None

    exact_task = str(task.get("exact_task") or "").strip()
    question = str(task.get("primary_question") or "").strip()
    question_context = "\n".join(str(item) for item in task.get("question_text", []) if item)
    instructions = "\n".join(str(item) for item in task.get("instructions", []) if item)
    combined = "\n".join(part for part in (exact_task, question, question_context, instructions) if part)

    if not combined or attachment_text is None:
        return None

    replacement = _find_replacement(combined)
    wants_character_count = _contains_any(_CHARACTER_PATTERNS, combined)
    wants_word_count = _contains_any(_WORD_PATTERNS, combined)
    exclude_spaces = _contains_any(_EXCLUDE_SPACE_PATTERNS, combined)

    if replacement and (wants_character_count or wants_word_count):
        old, new = replacement
        transformed, occurrences = _replace_all(attachment_text, old, new)
        if occurrences == 0:
            return None

        if wants_character_count:
            answer_value = len(transformed)
            if exclude_spaces:
                answer_value = sum(1 for char in transformed if not char.isspace())
            return DeterministicAnswer(
                answer=str(answer_value),
                solver="text_replace_and_count",
                operation="replace_all_then_count_characters",
                details={
                    "old": old,
                    "new": new,
                    "replacement_count": occurrences,
                    "count_includes_spaces": not exclude_spaces,
                },
            )

        answer_value = len(re.findall(r"\S+", transformed))
        return DeterministicAnswer(
            answer=str(answer_value),
            solver="text_replace_and_count",
            operation="replace_all_then_count_words",
            details={
                "old": old,
                "new": new,
                "replacement_count": occurrences,
            },
        )

    if wants_character_count:
        answer_value = len(attachment_text)
        if exclude_spaces:
            answer_value = sum(1 for char in attachment_text if not char.isspace())
        return DeterministicAnswer(
            answer=str(answer_value),
            solver="character_counter",
            operation="count_characters",
            details={"count_includes_spaces": not exclude_spaces},
        )

    if wants_word_count:
        answer_value = len(re.findall(r"\S+", attachment_text))
        return DeterministicAnswer(
            answer=str(answer_value),
            solver="word_counter",
            operation="count_words",
            details={},
        )

    return None
