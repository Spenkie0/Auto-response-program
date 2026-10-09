from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers import InProcessQuarantineRunner


@pytest.fixture(autouse=True)
def in_process_download_boundary(request, monkeypatch):
    """Use an in-process worker double for normal tests; Docker tests exercise the real runner."""
    module_name = request.module.__name__
    if module_name.endswith("test_docker_quarantine"):
        return
    monkeypatch.setattr("app.downloads.workflow.DockerQuarantineRunner", InProcessQuarantineRunner)
