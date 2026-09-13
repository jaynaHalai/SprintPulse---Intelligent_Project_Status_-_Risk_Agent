"""Persistent project memory backed by Mem0.

``ProjectMemory`` performs real Mem0 read/write operations. Two backends are
supported (hosted platform and self-hosted OSS); if neither can be reached the
class degrades to a local JSON store so a run never fails on a memory outage,
and the degradation is reported rather than hidden.
"""

from __future__ import annotations

import atexit
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from config import MemorySettings
from state import MemoryRecord


class MemoryUnavailable(RuntimeError):
    """Raised when the memory layer cannot serve a request at all."""


# A local Qdrant store may only be opened once per process, so every node in a
# run shares one client instance.
_CLIENT_CACHE: dict[str, Any] = {}


@atexit.register
def _close_cached_clients() -> None:
    """Close local vector stores before interpreter shutdown to avoid noise."""
    for entry in _CLIENT_CACHE.values():
        close = getattr(entry.get("client"), "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best effort at shutdown
                pass
    _CLIENT_CACHE.clear()


@dataclass
class MemoryEntry:
    """One thing worth remembering about a project."""

    text: str
    kind: str
    sprint: int

    def as_messages(self) -> list[dict[str, str]]:
        return [{"role": "user", "content": self.text}]

    def metadata(self, project_id: str) -> dict[str, Any]:
        return {"project": project_id, "sprint": self.sprint, "kind": self.kind}


class _LocalFallbackStore:
    """Keyword-scored JSON store used only when Mem0 is unreachable."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return []

    def add(self, text: str, metadata: dict[str, Any]) -> bool:
        rows = self._load()
        if any(row["text"] == text for row in rows):
            return False
        rows.append({"text": text, "metadata": metadata})
        self.path.write_text(json.dumps(rows, indent=2))
        return True

    def search(self, query: str, top_k: int) -> list[MemoryRecord]:
        terms = {t for t in re.findall(r"[a-z0-9\-]+", query.lower()) if len(t) > 2}
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in self._load():
            words = set(re.findall(r"[a-z0-9\-]+", row["text"].lower()))
            overlap = len(terms & words) / len(terms) if terms else 0.0
            if overlap > 0:
                scored.append((overlap, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            MemoryRecord(text=row["text"], score=round(score, 3), metadata=row.get("metadata", {}))
            for score, row in scored[:top_k]
        ]

    def all(self, top_k: int) -> list[MemoryRecord]:
        return [
            MemoryRecord(text=row["text"], metadata=row.get("metadata", {}))
            for row in self._load()[-top_k:]
        ]


class ProjectMemory:
    """Project-scoped wrapper over Mem0 with an explicit degraded mode."""

    def __init__(self, settings: MemorySettings, project_id: str, fault: bool = False):
        self.settings = settings
        self.project_id = project_id
        self.scope = f"project::{project_id}"
        self.fault = fault
        self.backend = "uninitialised"
        self.degraded_reason: Optional[str] = None
        self._client: Any = None
        self._fallback = _LocalFallbackStore(
            Path(settings.vector_store_path) / f"fallback_{project_id}.json"
        )

    # -- backend wiring ----------------------------------------------------
    def _mem0_config(self) -> dict[str, Any]:
        return {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": f"{self.settings.collection}_{self.project_id}",
                    # A local Qdrant folder can only be opened by one client, so
                    # each project gets its own directory.
                    "path": str(Path(self.settings.vector_store_path) / self.project_id),
                    "embedding_model_dims": self.settings.embedding_dims,
                    "on_disk": True,
                },
            },
            "embedder": {
                "provider": self.settings.embedder_provider,
                "config": {"model": self.settings.embedder_model},
            },
            # Mem0 always constructs an LLM, even when ``infer=False`` means it
            # is never called; a placeholder keeps offline runs working.
            "llm": {"provider": "openai", "config": {"api_key": "sk-unused-infer-disabled"}},
        }

    def _connect(self) -> Any:
        if self.fault:
            raise MemoryUnavailable("Injected fault: memory backend unreachable.")
        if self._client is not None:
            return self._client
        cache_key = f"{self.settings.mode}:{self.settings.vector_store_path}:{self.project_id}"
        cached = _CLIENT_CACHE.get(cache_key)
        if cached is not None:
            self._client, self.backend = cached["client"], cached["backend"]
            return self._client
        if self.settings.mode == "platform" and self.settings.api_key:
            from mem0 import MemoryClient

            self._client = MemoryClient(api_key=self.settings.api_key)
            self.backend = "mem0-platform"
        else:
            from mem0 import Memory

            self._client = Memory.from_config(self._mem0_config())
            self.backend = "mem0-oss"
        _CLIENT_CACHE[cache_key] = {"client": self._client, "backend": self.backend}
        return self._client

    def _degrade(self, exc: Exception) -> None:
        self.backend = "local-fallback"
        self.degraded_reason = str(exc)

    @property
    def is_degraded(self) -> bool:
        return self.backend == "local-fallback"

    # -- operations --------------------------------------------------------
    def write(self, entries: list[MemoryEntry]) -> int:
        """Persist entries, skipping ones already stored. Returns the count written."""
        if not entries:
            return 0
        try:
            client = self._connect()
            # Re-running the same sprint should not duplicate its history.
            existing = {record.text for record in self.all(top_k=500)}
            entries = [entry for entry in entries if entry.text not in existing]
            written = 0
            for entry in entries:
                client.add(
                    entry.as_messages(),
                    user_id=self.scope,
                    metadata=entry.metadata(self.project_id),
                    infer=self.settings.infer,
                )
                written += 1
            return written
        except Exception as exc:  # noqa: BLE001 - any backend failure degrades
            self._degrade(exc)
            return sum(
                1 for entry in entries if self._fallback.add(entry.text, entry.metadata(self.project_id))
            )

    def search(self, query: str, top_k: int = 8) -> list[MemoryRecord]:
        """Semantic search over everything remembered about this project."""
        try:
            client = self._connect()
            raw = client.search(query, filters={"user_id": self.scope}, top_k=top_k)
            return self._to_records(raw)
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)
            return self._fallback.search(query, top_k)

    def all(self, top_k: int = 50) -> list[MemoryRecord]:
        try:
            client = self._connect()
            raw = client.get_all(filters={"user_id": self.scope}, top_k=top_k)
            return self._to_records(raw)
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)
            return self._fallback.all(top_k)

    @staticmethod
    def _to_records(raw: Any) -> list[MemoryRecord]:
        rows = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
        records: list[MemoryRecord] = []
        for row in rows:
            records.append(
                MemoryRecord(
                    text=row.get("memory") or row.get("text") or "",
                    score=float(row.get("score") or 0.0),
                    metadata=row.get("metadata") or {},
                    memory_id=str(row.get("id", "")),
                )
            )
        return [record for record in records if record.text]
