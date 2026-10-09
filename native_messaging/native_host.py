from __future__ import annotations

import json
import struct
import sys
import traceback
from pathlib import Path

_NATIVE_STDOUT = sys.stdout.buffer
sys.stdout = sys.stderr

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.native_capture import capture_and_launch  # noqa: E402

MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


def encode_message(message: dict[str, object]) -> bytes:
    """Encode one JSON object using Firefox native-messaging length framing."""
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Native host response exceeds Firefox's 1 MB response limit.")
    return struct.pack("@I", len(payload)) + payload


def decode_message(raw: bytes) -> dict[str, object]:
    """Decode one complete Firefox native-messaging frame into a JSON object."""
    if len(raw) < 4:
        raise RuntimeError("Incomplete native messaging length prefix.")
    message_length = struct.unpack("@I", raw[:4])[0]
    payload = raw[4:]
    if message_length != len(payload):
        raise RuntimeError("Native messaging payload was truncated or had an invalid length.")
    return json.loads(payload.decode("utf-8"))


def read_message() -> dict[str, object] | None:
    """Read one length-prefixed native message from stdin, if present."""
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    if len(raw_length) != 4:
        raise RuntimeError("Incomplete native messaging length prefix.")
    message_length = struct.unpack("@I", raw_length)[0]
    if message_length > MAX_INPUT_BYTES:
        raise RuntimeError(f"Native message is too large: {message_length} bytes.")
    payload = sys.stdin.buffer.read(message_length)
    if len(payload) != message_length:
        raise RuntimeError("Native messaging payload was truncated.")
    return decode_message(raw_length + payload)


def send_message(message: dict[str, object]) -> None:
    """Encode and flush one native-messaging response to Firefox."""
    _NATIVE_STDOUT.write(encode_message(message))
    _NATIVE_STDOUT.flush()


def main() -> int:
    """Process one native capture request and return a host exit code."""
    try:
        message = read_message()
        if message is None:
            return 0
        response = capture_and_launch(message)
        send_message(response)
        return 0
    except Exception as exc:
        print("[NATIVE HOST ERROR] " + str(exc), file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        try:
            send_message({
                "ok": False,
                "status": "error",
                "error": str(exc),
                "exception": exc.__class__.__name__,
            })
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
