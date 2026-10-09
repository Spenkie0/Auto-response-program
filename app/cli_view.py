from __future__ import annotations

from typing import Any


def print_capture_header(*, raw_name: str, url: str | None, title: str | None, html_chars: int, screenshot_path: str | None) -> None:
    """Print a readable capture summary before processing stages begin."""
    print("=" * 64, flush=True)
    print(" SCRAPPER OLLAMA", flush=True)
    print("=" * 64, flush=True)
    print(f"[CAPTURE] file       : {raw_name}", flush=True)
    print(f"[CAPTURE] title      : {title or '(none)'}", flush=True)
    print(f"[CAPTURE] URL        : {url or '(none)'}", flush=True)
    print(f"[CAPTURE] HTML chars : {html_chars:,}", flush=True)
    print(f"[CAPTURE] screenshot : {screenshot_path or '(none)'}", flush=True)


def print_task_details(classification: Any) -> None:
    """Print deterministic task classification details, including sandbox frames."""
    print(
        f"[TASK] mode={classification.mode.value} response={classification.response_mode.value} "
        f"confidence={classification.confidence:.2f}",
        flush=True,
    )
    if classification.reasons:
        print(f"[TASK] reasons   : {' | '.join(classification.reasons)}", flush=True)
    print(f"[TASK] downloads : {classification.download_candidate_count} candidate(s); ambiguous={classification.ambiguous_download}", flush=True)
    if classification.download_candidate_urls:
        for url in classification.download_candidate_urls[:5]:
            print(f"         - {url}", flush=True)
    if classification.sandbox_frames:
        print(f"[SANDBOX] frames : {len(classification.sandbox_frames)}", flush=True)
        for index, frame in enumerate(classification.sandbox_frames, start=1):
            print(f"  [{index}] title={frame.title or '(none)'}", flush=True)
            print(f"      src={frame.src or '(none)'}", flush=True)
            print(f"      score={frame.sandbox_score} same_origin={frame.same_origin}", flush=True)
            print(f"      reasons={' | '.join(frame.reason_codes)}", flush=True)


def print_answer_result(decision: dict[str, Any]) -> None:
    """Print result diagnostics without printing the final answer twice."""
    print("[RESULT]", flush=True)
    print(f"  status     : {decision.get('status')}", flush=True)
    print(f"  confidence : {decision.get('confidence')}", flush=True)
    print(f"  model      : {decision.get('model', 'deterministic')}", flush=True)
    print(f"  reason     : {decision.get('reason') or '(none)'}", flush=True)


def print_final_answer(answer: object) -> None:
    """Print the user-facing answer exactly once, after diagnostics."""
    print("[FINAL ANSWER]", flush=True)
    print(str(answer).strip(), flush=True)
