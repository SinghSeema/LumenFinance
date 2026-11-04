import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

# Tuesday. Matches the evals.md example: "next Friday" -> 2026-11-13.
TODAY = date(2026, 11, 3)


@pytest.fixture
def today():
    return TODAY


@pytest.fixture
def anyio_backend():
    return "asyncio"


def pytest_collection_modifyitems(items):
    for item in items:
        if item.get_closest_marker("anyio") is None and getattr(item.obj, "__code__", None) \
                and item.obj.__code__.co_flags & 0x80:  # coroutine function
            item.add_marker(pytest.mark.anyio)
