# Task modes and embedded-sandbox detection

## What changed

The pipeline has a deterministic top-level task classifier with three modes:

- `basic`
- `file_task`
- `sandbox`

The classifier records response mode (`choice`, `text`, or `unknown`), likely interactive iframe candidates and their reasons, same-origin information where available, download-candidate counts and URLs, and ambiguous download signals.

The mode precedence is `SANDBOX > FILE_TASK > BASIC`, allowing an interactive embedded application to remain distinguishable from an ordinary question or a task that needs a downloaded resource.

## Sandbox detection

A sandbox is treated as a separate frame-level interaction surface. Detection uses general signals such as sandbox/simulator wording in iframe titles or CSS classes, including relevant ancestor classes. A plain video, map, or advertising iframe is not sufficient on its own.

The parent document's captured HTML does not necessarily contain the embedded frame's DOM. Any future interaction controller should observe the frame after each action and re-evaluate completion from fresh state.

## Testing

`tests/test_task_modes.py` covers all three modes, initial and started simulator states, cross-origin and same-origin frames, ordinary iframes, multiple iframes, sandbox/download overlap, download ambiguity, file inputs, response controls, extensionless URLs, malformed HTML, and serialized classification metadata.
