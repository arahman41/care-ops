"""Shared real-uvicorn-server test helper.

Extracted out of tests/test_orchestrator_integration.py (P2-6) rather than
duplicated: P4-2's end-to-end test needs the exact same "real socket, real
HTTP round trip" server it does, just wrapping the REAL agent apps instead of
stub ones. One code path for both, matching the project's own rule (see
governance/bootstrap.py, extracted the same way for P3-3).
"""
from __future__ import annotations

import threading
import time

import uvicorn
from fastapi import FastAPI

from shared.config import settings


class LiveServer:
    """A real uvicorn server on an ephemeral port, torn down after the test.

    Not named StubAgent here: P4-2 wraps genuine agent apps with only their
    model call mocked, so "stub" would misdescribe what is actually running.
    """

    def __init__(self, app: FastAPI):
        config = uvicorn.Config(app, host="127.0.0.1", port=0,
                                log_level="warning",
                                timeout_graceful_shutdown=1)
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "LiveServer":
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.01)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=10)


def point_at(monkeypatch, **urls) -> None:
    """Redirect the orchestrator's agent URLs (shared/config.py::settings)
    to wherever this test's LiveServer instances actually came up."""
    for agent, url in urls.items():
        monkeypatch.setattr(settings, f"{agent}_url", url)
