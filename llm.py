"""LLM access with structured output, retries and an explicit offline mode.

Nodes never construct a model directly: they ask for one and handle
``LLMUnavailable`` by falling back to deterministic logic.
"""

from __future__ import annotations

import os
from typing import Optional, Type, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from config import Settings
from resilience import RetryExhausted, call_with_retry

TModel = TypeVar("TModel", bound=BaseModel)

_PROVIDER_KEY_VAR = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class LLMUnavailable(RuntimeError):
    """No model could be reached; the caller must fall back."""


def build_model(settings: Settings) -> BaseChatModel:
    if "llm" in settings.faults:
        raise LLMUnavailable("Injected fault: the language model provider is unreachable.")
    if not settings.llm.available:
        raise LLMUnavailable(
            f"No API key configured for provider '{settings.llm.provider}'. "
            "Set it in .env to enable LLM-written analysis."
        )
    key_var = _PROVIDER_KEY_VAR.get(settings.llm.provider)
    if key_var and settings.llm.api_key:
        os.environ.setdefault(key_var, settings.llm.api_key)

    kwargs: dict[str, object] = {"max_tokens": settings.llm.max_tokens}
    if settings.llm.temperature is not None:
        kwargs["temperature"] = settings.llm.temperature
    try:
        return init_chat_model(
            settings.llm.model, model_provider=settings.llm.provider, **kwargs
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a clean fallback signal
        raise LLMUnavailable(f"Could not initialise {settings.llm.label}: {exc}") from exc


def structured_invoke(
    model: BaseChatModel,
    schema: Type[TModel],
    system_prompt: str,
    user_prompt: str,
    *,
    attempts: int = 2,
    label: str = "llm call",
) -> TModel:
    """Call the model and validate the answer against ``schema``."""
    runnable = model.with_structured_output(schema)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    def _run() -> TModel:
        result = runnable.invoke(messages)
        if not isinstance(result, schema):
            raise ValueError(f"Model returned {type(result).__name__}, expected {schema.__name__}.")
        return result

    try:
        return call_with_retry(_run, attempts=attempts, label=label)
    except RetryExhausted as exc:
        raise LLMUnavailable(str(exc)) from exc


def text_invoke(
    model: BaseChatModel,
    system_prompt: str,
    user_prompt: str,
    *,
    attempts: int = 2,
    label: str = "llm call",
) -> str:
    def _run() -> str:
        response = model.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        return response.text() if hasattr(response, "text") else str(response.content)

    try:
        return call_with_retry(_run, attempts=attempts, label=label)
    except RetryExhausted as exc:
        raise LLMUnavailable(str(exc)) from exc


def describe_model(settings: Settings) -> str:
    return settings.llm.label


def maybe_model(settings: Settings) -> Optional[BaseChatModel]:
    try:
        return build_model(settings)
    except LLMUnavailable:
        return None
