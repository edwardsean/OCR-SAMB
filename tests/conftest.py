"""The tests run on the live database: they must never write the trace (common/trace.py), which Jejak and Metrik
read as what really happened. This process is muted, and every call a test makes to the running API says
`X-Trace: off` (api/app.py mutes that request). tests/test_trace.py turns it back on for itself."""
import httpx
import pytest

from common import trace

OFF = {"X-Trace": "off"}


def _quiet(fn):
    def call(*a, headers=None, **kw):
        return fn(*a, headers={**OFF, **(headers or {})}, **kw)
    return call


@pytest.fixture(autouse=True)
def no_trace(request, monkeypatch):
    if request.module.__name__.endswith("test_trace"):
        return
    monkeypatch.setattr(trace, "MUTED", True)
    for name in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(httpx, name, _quiet(getattr(httpx, name)))
