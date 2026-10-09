import json
from pathlib import Path

from ..config import project_path

DEFAULT_RULES = {
    "selectors": [
        {
            "name": "Task item in assessment container",
            "selector": "div.assessment-container > article.task-item[data-task-id]",
            "required_selectors": [".task-question", ".task-response"],
        },
        {
            "name": "Structured task item",
            "selector": "article.task-item[data-task-id]",
            "required_selectors": [".task-question", ".task-response"],
        },
        {
            "name": "Generic task item",
            "selector": "article.task-item",
            "required_selectors": [".task-question", ".task-response"],
        },
    ],
    "heuristic": {
        "candidate_tags": ["main", "article", "section", "form", "div"],
        "keyword_weights": {
            "question": 18,
            "questions": 18,
            "quiz": 16,
            "exercise": 16,
            "task": 12,
            "problem": 12,
            "answer": 10,
            "choice": 8,
            "option": 8,
            "challenge": 8,
            "response": 7,
            "proposal": 7,
            "window": 6,
        },
        "class_id_bonus": 18,
        "question_mark_weight": 2.0,
        "input_bonus": 7,
        "radio_checkbox_bonus": 12,
        "button_bonus": 2,
        "min_text_length": 20,
        "max_text_length": 250000,
        "top_k": 8,
        "min_score": 55.0,
        "min_margin": 12.0,
    },
}


def load_rules(path: str | None = None) -> dict[str, object]:
    """Load configured window rules and merge them with built-in defaults."""
    rules_path = Path(path) if path else project_path("config", "window_rules.json")
    if not rules_path.exists():
        return json.loads(json.dumps(DEFAULT_RULES))
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULT_RULES))
    merged.update({k: v for k, v in data.items() if k != "heuristic"})
    merged["heuristic"].update(data.get("heuristic", {}))
    return merged
