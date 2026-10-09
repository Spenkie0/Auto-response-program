from pathlib import Path

from ..config import project_path


def load_prompt(name: str, fallback: str) -> str:
    """Load a prompt file or use the supplied embedded fallback text."""
    path = project_path("prompts", name)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback


def window_verifier_prompt() -> str:
    """Return the prompt used to verify ambiguous HTML window candidates."""
    return load_prompt(
        "window_verifier.txt",
        """You are an HTML window verifier. You are NOT answering the page's questions.
Choose which candidate element most likely contains the intended question/task window.
Return JSON only: {\"status\":\"accept\"|\"reject\",\"candidate_index\":number|null,\"confidence\":number,\"reason\":string}.
Accept only when one candidate clearly represents the relevant window.
""",
    )


def answerer_prompt() -> str:
    """Return the prompt used by the main model to answer cleaned-page questions."""
    return load_prompt(
        "answerer.txt",
        """You answer questions contained in the supplied cleaned HTML window.
Do not guess. If you are sufficiently certain, return status=answer. If the page does not contain enough information or you are not sufficiently certain, return status=answer_not_found.
Be concise: return only the main information that distinguishes the correct answer from alternatives. Do not restate the question or give a detailed explanation. For multiple selections, list only the exact visible selected choice texts. For short factual answers, return only the needed value or phrase. For reasoning answers, give the direct conclusion plus at most one short supporting clause. The reason must be one short evidence-based sentence and must not repeat the answer.
Return JSON only: {\"status\":\"answer\"|\"answer_not_found\",\"confidence\":number,\"answer\":string|null,\"reason\":string}.
""",
    )
