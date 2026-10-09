import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import DEBUG_OLLAMA_FOLDER


def new_timer() -> tuple[float, str]:
    """Return a monotonic timer and a UTC timestamp for profiling."""
    return time.perf_counter(), datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def elapsed_ms(start: float) -> float:
    """Return elapsed time since a perf-counter start in milliseconds."""
    return round((time.perf_counter() - start) * 1000.0, 2)


def save_profile(base_dir: Path, payload: dict[str, object], overwrite: bool = False) -> Path:
    """Save one Ollama performance profile under data/debug/ollama."""
    folder = base_dir / "data" / DEBUG_OLLAMA_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    stage = str(payload.get('stage', 'ollama')).replace('/', '_')
    path = folder / f"{stamp}_{stage}.json"
    if overwrite and path.exists():
        path.unlink()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def _seconds(ns_value: object) -> float | None:
    """Convert a nanosecond duration value to seconds when numeric."""
    try:
        return round(float(ns_value) / 1_000_000_000.0, 3)
    except (TypeError, ValueError):
        return None


def normalize_ollama_metrics(raw: dict[str, object] | None, wall_ms: float | None = None) -> dict[str, object]:
    """Normalize Ollama timing and token counters into readable metrics."""
    raw = raw or {}
    metrics = {
        'wall_seconds': round(wall_ms / 1000.0, 3) if wall_ms is not None else None,
        'total_seconds': _seconds(raw.get('total_duration')),
        'load_seconds': _seconds(raw.get('load_duration')),
        'prompt_eval_seconds': _seconds(raw.get('prompt_eval_duration')),
        'generation_seconds': _seconds(raw.get('eval_duration')),
        'prompt_tokens': raw.get('prompt_eval_count'),
        'cached_prompt_tokens': raw.get('prompt_eval_cached_count'),
        'output_tokens': raw.get('eval_count'),
        'raw_present': bool(raw),
    }
    if metrics['generation_seconds'] and metrics['output_tokens'] is not None:
        seconds = metrics['generation_seconds']
        if seconds > 0:
            metrics['generation_tokens_per_second'] = round(metrics['output_tokens'] / seconds, 2)
    if metrics['prompt_eval_seconds'] and metrics['prompt_tokens'] is not None:
        seconds = metrics['prompt_eval_seconds']
        if seconds > 0:
            metrics['prompt_tokens_per_second'] = round(metrics['prompt_tokens'] / seconds, 2)
    return metrics


def print_metrics(stage: str, model: str, metrics: dict[str, object], input_chars: int, compact_chars: int | None = None) -> None:
    """Print concise Ollama timing and throughput information."""
    print(f"[OLLAMA] {stage} model={model}", flush=True)
    print(f"         input chars: {input_chars:,}" + (f" -> compact chars: {compact_chars:,}" if compact_chars is not None else ""), flush=True)
    prompt_tokens = metrics.get('prompt_tokens')
    if prompt_tokens is not None:
        print(f"         prompt tokens: {prompt_tokens:,}", flush=True)
    if metrics.get('load_seconds') is not None:
        print(f"         load: {metrics['load_seconds']:.3f}s", end="", flush=True)
    if metrics.get('prompt_eval_seconds') is not None:
        print(f" | prompt: {metrics['prompt_eval_seconds']:.3f}s", end="", flush=True)
    if metrics.get('generation_seconds') is not None:
        print(f" | generation: {metrics['generation_seconds']:.3f}s", end="", flush=True)
    if metrics.get('total_seconds') is not None:
        print(f" | total: {metrics['total_seconds']:.3f}s", end="", flush=True)
    elif metrics.get('wall_seconds') is not None:
        print(f" | wall: {metrics['wall_seconds']:.3f}s", end="", flush=True)
    print("", flush=True)
    if metrics.get('generation_tokens_per_second') is not None:
        print(f"         generation speed: {metrics['generation_tokens_per_second']:.2f} tok/s", flush=True)
