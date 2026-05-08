"""Global pytest test-harness setup for the repository."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Override anyio's default ``anyio_backend`` fixture so that @pytest.mark.anyio
# tests only run under asyncio (trio is not installed).
@pytest.fixture(scope="module", params=["asyncio"])
def anyio_backend(request):
    return request.param


if "ddgs" not in sys.modules:
    ddgs_stub = types.ModuleType("ddgs")

    class _StubDDGS:
        def text(self, *_args, **_kwargs):
            return []

    ddgs_stub.DDGS = _StubDDGS
    sys.modules["ddgs"] = ddgs_stub

    ddgs_exceptions = types.ModuleType("ddgs.exceptions")

    class DDGSException(Exception):
        pass

    ddgs_exceptions.DDGSException = DDGSException
    sys.modules["ddgs.exceptions"] = ddgs_exceptions
