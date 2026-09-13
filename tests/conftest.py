"""Test fixtures: keep every test off the real memory store and off the network."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def isolated_memory_store(tmp_path_factory):
    store = tmp_path_factory.mktemp("mem0_store")
    os.environ["MEM0_VECTOR_PATH"] = str(store)
    os.environ["MEM0_COLLECTION"] = "sprintpulse_test"
    os.environ["MEM0_MODE"] = "oss"
    # No provider keys during tests: the deterministic paths are what we assert on.
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SPRINTPULSE_LLM_API_KEY", "MEM0_API_KEY"):
        os.environ.pop(key, None)
    yield store


@pytest.fixture
def data_dir() -> Path:
    return ROOT / "data"


@pytest.fixture
def source(data_dir):
    from tools.project_data import JSONProjectDataSource

    return JSONProjectDataSource(data_dir)
