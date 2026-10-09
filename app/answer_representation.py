from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup


_WHITESPACE = re.compile(r"\s+")
_QUESTION_MARKS = ("?", "؟", "？")


def _clean_text(value: str) -> str:
    """Normalize whitespace and trim surrounding spaces."""
    return _WHITESPACE.sub(" ", value or "").strip()


def _unique(items: list[str]) -> list[str]:
    """Return cleaned strings in original order with duplicates removed."""
    seen = set()
    result = []
    for item in items:
        item = _clean_text(item)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _primary_question(question_blocks: list[str], question_nodes: list[Any]) -> str | None:
    """Extract the strongest explicit question sentence without discarding context."""
    sentence_candidates: list[str] = []
    for node in question_nodes:
        for paragraph in node.find_all(["p", "li"], recursive=True):
            text = _clean_text(paragraph.get_text(" ", strip=True))
            if text and any(mark in text for mark in _QUESTION_MARKS):
                sentence_candidates.append(text)
    if sentence_candidates:
        return _unique(sentence_candidates)[-1]

    for block in reversed(question_blocks):
        parts = re.split(r"(?<=[?؟？])\s+", block)
        for part in reversed(parts):
            part = _clean_text(part)
            if any(mark in part for mark in _QUESTION_MARKS):
                return part
    return question_blocks[-1] if question_blocks else None


def summarize_attachment_metadata(attachment_metadata: dict[str, object] | None) -> dict[str, object] | None:
    """Keep only concise attachment metadata useful for answering the webpage task."""
    if not attachment_metadata:
        return None
    allowed = {
        "detected_kind",
        "detected_extension",
        "effective_extension",
        "archive_kind",
        "truncated",
        "character_count",
        "content_type",
        "download_size",
        "candidate_label",
        "candidate_reason",
    }
    return {key: attachment_metadata[key] for key in allowed if key in attachment_metadata}



def filter_attachment_evidence_for_ai(evidence: dict[str, object] | None) -> dict[str, object] | None:
    """Allowlist only task-relevant attachment evidence; never expose host/debug/quarantine data."""
    if not isinstance(evidence, dict):
        return None
    result: dict[str, object] = {}
    file_section = evidence.get("file")
    if isinstance(file_section, dict):
        allowed_file = ("extension", "url_extension", "url_extension_match", "detected_kind", "canonical_extension", "mime_type", "mime_match", "size_bytes", "validation_strength")
        result["file"] = {key: file_section[key] for key in allowed_file if key in file_section}
    for section_name in ("text", "tables", "images", "ocr", "visual_analysis", "embedded_content", "structure"):
        section = evidence.get(section_name)
        if section is None:
            continue
        if section_name == "structure" and isinstance(section, dict):
            result[section_name] = {key: section[key] for key in ("status", "container_type", "relationships") if key in section}
        elif section_name == "images" and isinstance(section, dict):
            items = section.get("items")
            safe_items = []
            if isinstance(items, list):
                for item in items[:64]:
                    if isinstance(item, dict):
                        safe_items.append({key: item[key] for key in ("name", "extension", "mime_type", "source", "size_bytes", "depth") if key in item})
            result[section_name] = {"status": section.get("status"), "count": section.get("count"), "items": safe_items}
        elif section_name == "ocr" and isinstance(section, dict):
            result[section_name] = {key: section[key] for key in ("status", "text", "character_count") if key in section}
        elif section_name == "visual_analysis" and isinstance(section, dict):
            result[section_name] = {key: section[key] for key in ("status", "items") if key in section}
        elif isinstance(section, dict):
            result[section_name] = {key: section[key] for key in section.keys() if key in {"status", "content", "character_count", "count", "items", "language"}}
    return result

def _fit_representation(compact: dict[str, object], max_text_chars: int) -> str:
    """Keep the task intact while trimming secondary context to fit the model budget."""
    def encode() -> str:
        return json.dumps(compact, ensure_ascii=False, indent=2)

    encoded = encode()
    if len(encoded) <= max_text_chars:
        return encoded

    attachment = compact.get("attachment")
    if isinstance(attachment, dict):
        text = str(attachment.get("text") or "")
        # Reserve space for the task before spending the remaining budget on the file.
        attachment["text"] = text[: max(0, max_text_chars // 2)]
        encoded = encode()

    if len(encoded) <= max_text_chars:
        return encoded

    compact["other_visible_text"] = []
    compact["images"] = []
    encoded = encode()
    if len(encoded) <= max_text_chars:
        return encoded

    if isinstance(attachment, dict):
        # Preserve the full task/question/choices and use the remainder for attachment text.
        base = dict(compact)
        base_attachment = dict(attachment)
        base_attachment["text"] = ""
        base["attachment"] = base_attachment
        base_encoded = json.dumps(base, ensure_ascii=False, indent=2)
        remaining = max(0, max_text_chars - len(base_encoded) - 20)
        attachment["text"] = str(attachment.get("text") or "")[:remaining]

    return encode()


def build_compact_representation(
    clean_html: str,
    *,
    filename: str,
    page_url: str | None = None,
    title: str | None = None,
    max_text_chars: int = 40000,
    attachment_text: str | None = None,
    attachment_metadata: dict[str, object] | None = None,
    attachment_evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a task-first semantic representation for the answer model.

    The question/task is always preserved ahead of secondary page and attachment data.
    The original clean HTML is never modified, and attachment metadata is minimized.
    """
    soup = BeautifulSoup(clean_html, "html.parser")

    for tag in soup.find_all(["script", "style", "noscript", "template", "svg", "iframe", "canvas"]):
        tag.decompose()

    question_nodes = soup.select(
        ".question-text, "
        ".question-instructions"
    )
    question_text = _unique([node.get_text(" ", strip=True) for node in question_nodes])
    primary_question = _primary_question(question_text, question_nodes)

    instruction_nodes = soup.select(
        ".response-instructions, "
        ".task-response legend, "
        "fieldset > legend, "
        ".question-text + *"
    )
    instructions = _unique([node.get_text(" ", strip=True) for node in instruction_nodes])

    choices: list[dict[str, object]] = []
    for control in soup.select("input, select, textarea"):
        control_type = (control.get("type") or control.name or "").lower()
        label_text = ""
        control_id = control.get("id")
        if control_id:
            label = soup.find("label", attrs={"for": control_id})
            if label:
                label_text = label.get_text(" ", strip=True)
        if not label_text:
            parent_label = control.find_parent("label")
            if parent_label:
                label_text = parent_label.get_text(" ", strip=True)
        text = _clean_text(label_text)
        if not text and control.name == "textarea":
            text = _clean_text(control.get("placeholder") or control.get("aria-label") or "")
        if not text and control.get("aria-label"):
            text = _clean_text(control.get("aria-label"))
        if text:
            choices.append({
                "type": control_type,
                "name": control.get("name"),
                "value": control.get("value"),
                "text": text,
            })

    # Some sites render choices as buttons. Only inspect buttons inside likely proposal groups,
    # so validation/action buttons are not mistaken for answer choices.
    for button in soup.select(".task-options button, .proposal button, [role='radiogroup'] button"):
        text = _clean_text(button.get_text(" ", strip=True))
        if text:
            choices.append({
                "type": "button",
                "name": button.get("name"),
                "value": button.get("value"),
                "text": text,
            })

    images = [
        {"alt": _clean_text(image.get("alt") or ""), "src": image.get("src") or ""}
        for image in soup.find_all("img")
    ]

    visible_text = _unique([
        node.get_text(" ", strip=True)
        for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "legend", "label", "td", "th"])
    ])
    choice_texts = {item["text"] for item in choices if item.get("text")}
    other_text = [text for text in visible_text if text not in choice_texts]

    attachment = None
    if attachment_text is not None:
        attachment = {
            "role": "supporting_document_content",
            "metadata": summarize_attachment_metadata(attachment_metadata),
            "evidence": filter_attachment_evidence_for_ai(attachment_evidence),
            "text": attachment_text,
        }

    exact_task = "\n".join(question_text).strip() or primary_question

    response_mode = "choice" if choices else "open_text"

    compact: dict[str, object] = {
        "task": {
            "exact_task": exact_task,
            "primary_question": primary_question,
            "question_text": question_text,
            "instructions": instructions,
            "response_mode": response_mode,
            "choices": choices,
            "answer_output_rule": (
                "Return the exact visible sentence/text of the selected choice, not its numeric value or position."
                if response_mode == "choice"
                else "Return the answer requested by the task."
            ),
        },
        "page_context": {
            "filename": filename,
            "page_url": page_url,
            "title": title,
        },
        "attachment": attachment,
        "images": images,
        "other_visible_text": other_text,
    }

    compact["model_text"] = _fit_representation(compact, max_text_chars)
    return compact
