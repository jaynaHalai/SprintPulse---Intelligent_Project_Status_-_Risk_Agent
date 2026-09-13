"""Small retry helper shared by the tool-calling nodes."""

from __future__ import annotations

import time
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")


class RetryExhausted(RuntimeError):
    def __init__(self, message: str, attempts: int, last_error: Exception):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


def call_with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 2,
    delay: float = 0.4,
    retry_on: Iterable[type[BaseException]] = (Exception,),
    label: str = "operation",
) -> T:
    """Run ``fn``, retrying transient failures.

    Raises ``RetryExhausted`` once every attempt has failed so callers can
    decide whether the failure is fatal or merely degrades the run.
    """
    retry_on = tuple(retry_on)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:  # noqa: PERF203 - explicit retry loop
            last = exc
            if attempt < attempts:
                time.sleep(delay)
    raise RetryExhausted(f"{label} failed after {attempts} attempt(s): {last}", attempts, last)  # type: ignore[arg-type]
