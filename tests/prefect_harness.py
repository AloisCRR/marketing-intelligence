"""Prefect no-engine harness: same flow logic, no orchestration startup.

Docs-aligned with official Prefect v3 pattern B
(https://docs.prefect.io/v3/how-to-guides/workflows/test-workflows):
call the wrapped function via ``.fn()`` inside ``disable_run_logger()`` so
``get_run_logger()`` returns a disabled null logger instead of raising
``MissingContextError`` — with zero engine startup.

The thin sync adapter below (``_PlainCall``/``_SyncFuture``/``_SyncState``)
exists only because ``src/brain/flows.py`` re-enters orchestration in its
interior, so a naive top-level-only ``.fn()`` swap would re-enter the engine
on nested calls and lose the speed gain:

- ``_article_task.submit`` + ``wait`` + ``result`` (flows.py 179-186)
- ``_enrich_one_task.submit`` + ``wait`` + ``result`` (flows.py 258-266)
- ``upsert_task.with_options(task_run_name=...)`` (flows.py 422)
- ``ingest_source_flow.with_options(...)(..., return_state=True)`` +
  ``state.is_completed``/``result``/``message`` (flows.py 470-479)
- ``_ingest_one_task.with_options(...).submit`` + ``wait`` + ``result``
  (flows.py 530-539)
- ``get_run_logger`` in every task/flow (flows.py 82, 103-109, 192-197,
  248-252, 276-278, 286, 350, 364, 490, 522, 554)

Each adapter is strictly a synchronous facade over ``target.fn`` (same
function, no behavior change); ``with_options``/``submit``/``return_state``
are pass-through shims so fan-out code runs unchanged. Retry
declaration/behavior tests stay on the real engine for coverage.
"""

from __future__ import annotations

from typing import Any

import pytest
from prefect.logging import disable_run_logger

import brain.flows as flows


class _SyncState:
    """Minimal `return_state` stand-in for subflow isolation."""

    def __init__(self, ok: bool, value: Any) -> None:
        self._ok = ok
        self._value = value
        self.message = "" if ok else str(value)

    def is_completed(self) -> bool:
        return self._ok

    def result(self) -> Any:
        if not self._ok:
            raise self._value
        return self._value


class _SyncFuture:
    """Synchronous stand-in for a submitted Prefect future."""

    def __init__(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        try:
            with disable_run_logger():
                self._value: Any = fn(*args, **kwargs)
            self._exc: Exception | None = None
        except Exception as exc:
            self._value = None
            self._exc = exc

    def wait(self) -> None:
        pass

    def result(self) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._value


class _PlainCall:
    """Plain synchronous view of a Prefect Task/Flow (same `.fn`)."""

    def __init__(self, task_or_flow: Any) -> None:
        self._fn = task_or_flow.fn

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.pop("return_state", False):
            try:
                with disable_run_logger():
                    value = self._fn(*args, **kwargs)
                return _SyncState(True, value)
            except Exception as exc:
                return _SyncState(False, exc)
        with disable_run_logger():
            return self._fn(*args, **kwargs)

    def with_options(self, **kwargs: Any) -> _PlainCall:
        return self

    def submit(self, *args: Any, **kwargs: Any) -> _SyncFuture:
        return _SyncFuture(self._fn, *args, **kwargs)


_TARGETS = "fetch_task parse_task upsert_task discover_task enrich_task _article_task _enrich_one_task _ingest_one_task ingest_source_flow ingest_sources_flow".split()


def disable_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch `brain.flows` tasks/flows to plain calls (auto-undone)."""
    for name in _TARGETS:
        target = getattr(flows, name)
        if isinstance(target, _PlainCall):
            continue
        if callable(getattr(target, "fn", None)):
            plain: Any = _PlainCall(target)
        else:
            plain = target.with_options(retries=0, retry_delay_seconds=0)
        monkeypatch.setattr(flows, name, plain, raising=True)


@pytest.fixture
def no_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill Prefect engine startup for flow tests; logic runs unchanged."""
    disable_engine(monkeypatch)
