from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ..downloads.hints import find_download_candidates, page_may_require_download, select_download_candidate
from ..downloads.rules import load_download_rules
from .models import SandboxFrame, TaskClassification, TaskMode, TaskResponseMode


_SANDBOX_TITLE_WORDS = {
    "sandbox",
    "simulateur",
    "simulation",
    "simulator",
    "interactive simulator",
}

_SANDBOX_CLASS_WORDS = {
    "sandbox",
    "simulator",
    "simulateur",
}



def _origin(url: str) -> str | None:
    """Return a normalized HTTP(S) origin, or None for non-network URLs."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"



def _same_origin(page_url: str, frame_url: str) -> bool | None:
    """Compare page and frame origins when both are ordinary HTTP(S) URLs."""
    page_origin = _origin(page_url)
    frame_origin = _origin(frame_url)
    if page_origin is None or frame_origin is None:
        return None
    return page_origin == frame_origin



def _class_tokens(tag: Tag) -> tuple[str, ...]:
    """Return stable, de-duplicated CSS class tokens."""
    values = [str(value) for value in (tag.get("class") or []) if str(value).strip()]
    return tuple(dict.fromkeys(values))


def _has_sandbox_semantics(class_tokens: tuple[str, ...]) -> bool:
    """Detect generic sandbox/simulator words in CSS class tokens."""
    lowered = (token.lower() for token in class_tokens)
    return any(word in token for token in lowered for word in _SANDBOX_CLASS_WORDS)


def _ancestor_has_sandbox_class(tag: Tag) -> bool:
    """Return whether an ancestor uses a generic sandbox/simulator class."""
    for ancestor in tag.parents:
        if isinstance(ancestor, Tag) and _has_sandbox_semantics(_class_tokens(ancestor)):
            return True
    return False



def _sandbox_frame_candidates(soup: BeautifulSoup, page_url: str) -> list[SandboxFrame]:
    """Identify iframe elements that strongly resemble interactive task sandboxes."""
    frames: list[SandboxFrame] = []
    for iframe in soup.find_all("iframe"):
        if not isinstance(iframe, Tag):
            continue

        title = " ".join(str(iframe.get("title") or "").split())
        src_raw = str(iframe.get("src") or "").strip()
        src = urljoin(page_url, src_raw) if src_raw else ""
        classes = _class_tokens(iframe)
        lowered_title = title.lower()

        score = 0
        reasons: list[str] = []

        if _ancestor_has_sandbox_class(iframe):
            score += 100
            reasons.append("sandbox-like-ancestor-class")
        if any(word in lowered_title for word in _SANDBOX_TITLE_WORDS):
            score += 70
            reasons.append("sandbox-like-title")
        if _has_sandbox_semantics(classes):
            score += 75
            reasons.append("sandbox-like-frame-class")

        # A bare iframe is not enough. Many pages embed videos, ads, maps, etc.
        if score < 70:
            continue

        frames.append(
            SandboxFrame(
                src=src,
                title=title,
                classes=classes,
                sandbox_score=score,
                reason_codes=tuple(reasons),
                same_origin=_same_origin(page_url, src),
            )
        )

    return sorted(frames, key=lambda frame: (-frame.sandbox_score, frame.src, frame.title))



def _detect_response_mode(soup: BeautifulSoup) -> TaskResponseMode:
    """Determine whether the visible answer control is primarily a choice or text input."""
    radio_or_checkbox = soup.select(
        "input[type=radio], input[type=checkbox], [role='radio'], [role='checkbox']"
    )
    if radio_or_checkbox:
        return TaskResponseMode.CHOICE

    proposal_buttons = soup.select(
        ".task-options button, .proposal button, [role='radiogroup'] button"
    )
    if proposal_buttons:
        return TaskResponseMode.CHOICE

    select_controls = soup.select("select")
    if select_controls:
        return TaskResponseMode.CHOICE

    text_controls = soup.select("textarea, input:not([type]), input[type=text], input[type=search]")
    if text_controls:
        return TaskResponseMode.TEXT

    return TaskResponseMode.UNKNOWN



def classify_task(
    clean_html: str,
    *,
    page_url: str = "",
    accepted_extensions: list[str] | None = None,
    download_minimum_score: int = 70,
) -> TaskClassification:
    """Classify one clean page into BASIC, FILE_TASK, or SANDBOX.

    Classification is deterministic and intentionally precedes any LLM reasoning.
    When multiple signals coexist, SANDBOX has priority over FILE_TASK because the
    page requires stateful interaction inside an embedded application. FILE_TASK has
    priority over BASIC because the page requires a downloaded supporting resource.
    """
    soup = BeautifulSoup(clean_html or "", "html.parser")
    accepted = (
        list(accepted_extensions)
        if accepted_extensions is not None
        else list(load_download_rules().get("accepted_extensions", []))
    )

    sandbox_frames = _sandbox_frame_candidates(soup, page_url)

    candidates = find_download_candidates(clean_html, page_url, accepted)
    selected = select_download_candidate(candidates, minimum_score=download_minimum_score)
    download_signal = page_may_require_download(clean_html)
    ambiguous_download = download_signal and selected is None and bool(candidates)

    response_mode = _detect_response_mode(soup)
    local_file_control = bool(soup.select("input[type=file]"))
    file_task_signal = download_signal or local_file_control
    reasons: list[str] = []

    if sandbox_frames:
        mode = TaskMode.SANDBOX
        confidence = min(1.0, 0.80 + min(sandbox_frames[0].sandbox_score, 200) / 1000.0)
        reasons.append("interactive-sandbox-iframe-detected")
        if file_task_signal:
            reasons.append("file-task-signal-also-present")
        if response_mode != TaskResponseMode.UNKNOWN:
            reasons.append(f"outer-response-mode:{response_mode.value}")
    elif file_task_signal:
        mode = TaskMode.FILE_TASK
        confidence = 0.95 if selected is not None else 0.80
        if download_signal:
            reasons.append("download-or-file-task-signal-detected")
        if local_file_control:
            reasons.append("local-file-input-detected")
        if selected is not None:
            reasons.append("unambiguous-download-candidate")
        elif ambiguous_download:
            reasons.append("download-candidates-ambiguous")
    else:
        mode = TaskMode.BASIC
        confidence = 0.90
        reasons.append("no-sandbox-or-file-task-signal")

    return TaskClassification(
        mode=mode,
        response_mode=response_mode,
        confidence=round(confidence, 3),
        reasons=tuple(reasons),
        sandbox_frames=tuple(sandbox_frames),
        download_candidate_count=len(candidates),
        download_candidate_urls=tuple(candidate.url for candidate in candidates[:10]),
        ambiguous_download=ambiguous_download,
    )
