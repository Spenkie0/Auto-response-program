from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.downloads.docker_quarantine import build_quarantine_image
from app.config import default_base_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Scrapper Ollama Docker quarantine image once.")
    parser.add_argument("--image", default="scrapper-ollama-quarantine:2.0")
    args = parser.parse_args()
    root = default_base_dir()
    build_quarantine_image(root, args.image)
    print(f"[QUARANTINE] Docker image ready: {args.image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
