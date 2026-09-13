"""Environment-driven configuration for SprintPulse.

Every external dependency (LLM provider, Mem0 backend, data directory) is
resolved here so the rest of the code never reads ``os.environ`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent

# Mem0 phones home by default; keep local runs offline-friendly.
os.environ.setdefault("MEM0_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_opt(name: str) -> Optional[str]:
    value = _env(name)
    return value or None


@dataclass(frozen=True)
class LLMSettings:
    """How to reach the language model. Empty ``api_key`` means offline mode."""

    provider: str
    model: str
    api_key: Optional[str]
    max_tokens: int
    temperature: Optional[float]

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" if self.available else "deterministic fallback"


@dataclass(frozen=True)
class MemorySettings:
    """Mem0 configuration.

    Two supported modes:
      * ``platform``  - hosted Mem0 (``MEM0_API_KEY``), via ``MemoryClient``.
      * ``oss``       - self-hosted ``Memory`` with a local Qdrant store.

    In ``oss`` mode the embedder defaults to ``fastembed`` so memory works with
    no API key at all; fact extraction (``infer``) is only enabled when an LLM
    key is present, because that step needs a model.
    """

    mode: str
    api_key: Optional[str]
    vector_store_path: str
    collection: str
    embedder_provider: str
    embedder_model: str
    embedding_dims: int
    infer: bool


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm: LLMSettings
    memory: MemorySettings
    stale_update_days: int = 5
    blocked_escalation_days: int = 3
    faults: tuple[str, ...] = field(default=())


def _llm_settings() -> LLMSettings:
    provider = _env("SPRINTPULSE_LLM_PROVIDER", "anthropic").lower()
    key_var = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }.get(provider, "SPRINTPULSE_LLM_API_KEY")
    default_model = {
        "anthropic": "claude-opus-5",
        "openai": "gpt-4.1-mini",
    }.get(provider, "")
    temperature = _env_opt("SPRINTPULSE_LLM_TEMPERATURE")
    return LLMSettings(
        provider=provider,
        model=_env("SPRINTPULSE_LLM_MODEL", default_model),
        api_key=_env_opt(key_var) or _env_opt("SPRINTPULSE_LLM_API_KEY"),
        max_tokens=int(_env("SPRINTPULSE_LLM_MAX_TOKENS", "8000")),
        # Newer Claude models reject sampling parameters, so only send one when
        # the operator explicitly asks for it.
        temperature=float(temperature) if temperature else None,
    )


def _memory_settings(llm_available: bool) -> MemorySettings:
    mode = _env("MEM0_MODE", "platform" if _env("MEM0_API_KEY") else "oss").lower()
    infer_env = _env_opt("MEM0_INFER")
    infer = (infer_env.lower() == "true") if infer_env else llm_available
    return MemorySettings(
        mode=mode,
        api_key=_env_opt("MEM0_API_KEY"),
        vector_store_path=_env("MEM0_VECTOR_PATH", str(ROOT / ".mem0_store")),
        collection=_env("MEM0_COLLECTION", "sprintpulse"),
        embedder_provider=_env("MEM0_EMBEDDER_PROVIDER", "fastembed"),
        embedder_model=_env("MEM0_EMBEDDER_MODEL", "BAAI/bge-small-en-v1.5"),
        embedding_dims=int(_env("MEM0_EMBEDDING_DIMS", "384")),
        infer=infer,
    )


def get_settings(faults: tuple[str, ...] = ()) -> Settings:
    """Build settings. ``faults`` injects test failures ('data'|'llm'|'memory')."""
    llm = _llm_settings()
    return Settings(
        data_dir=Path(_env("SPRINTPULSE_DATA_DIR", str(ROOT / "data"))),
        llm=llm,
        memory=_memory_settings(llm.available),
        stale_update_days=int(_env("SPRINTPULSE_STALE_DAYS", "5")),
        blocked_escalation_days=int(_env("SPRINTPULSE_BLOCKED_ESCALATION_DAYS", "3")),
        faults=faults,
    )
