"""Memory layer: real Mem0 read/write plus the degraded fallback."""

from __future__ import annotations

from config import get_settings
from state import MemoryRecord
from tools.memory import MemoryEntry, ProjectMemory


def _memory(project_id: str, fault: bool = False) -> ProjectMemory:
    return ProjectMemory(get_settings().memory, project_id, fault=fault)


# 5. Memory retrieval logic -------------------------------------------------
def test_mem0_write_then_search_round_trip():
    memory = _memory("memtest")
    written = memory.write(
        [
            MemoryEntry(
                kind="blocker",
                sprint=4,
                text="In Sprint 4, task ATL-204 was blocked waiting on NorthBank sandbox credentials.",
            ),
            MemoryEntry(
                kind="health",
                sprint=4,
                text="Sprint 4 finished with health 'at_risk' (score 46/100).",
            ),
        ]
    )
    assert written == 2
    assert memory.backend.startswith("mem0"), "the real Mem0 backend should be used"

    hits = memory.search("what was blocked and why", top_k=5)
    assert hits and all(isinstance(hit, MemoryRecord) for hit in hits)
    assert any("ATL-204" in hit.text for hit in hits)
    assert all(hit.score >= 0 for hit in hits)


def test_duplicate_entries_are_not_rewritten():
    memory = _memory("memtest-dupes")
    entry = MemoryEntry(kind="health", sprint=7, text="Sprint 7 health was healthy (score 92/100).")
    assert memory.write([entry]) == 1
    assert memory.write([entry]) == 0


def test_memory_is_scoped_per_project():
    ProjectMemory(get_settings().memory, "project-a").write(
        [MemoryEntry(kind="risk", sprint=1, text="Project A risk: vendor contract unsigned.")]
    )
    hits = ProjectMemory(get_settings().memory, "project-b").search("vendor contract", top_k=5)
    assert not any("Project A" in hit.text for hit in hits)


def test_memory_failure_degrades_to_local_store():
    memory = _memory("memtest-fault", fault=True)
    assert memory.write([MemoryEntry(kind="risk", sprint=5, text="Fallback risk entry about ATL-999.")]) == 1
    assert memory.is_degraded and memory.backend == "local-fallback"
    assert memory.degraded_reason

    hits = memory.search("ATL-999 risk entry", top_k=3)
    assert hits and "ATL-999" in hits[0].text
