"""Optional operation feedback, scoped to the calling workflow, never stdout."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    message: str
    detail: str = ""
    output: bool = False


ProgressSink = Callable[[Progress], None]
observer: ContextVar[ProgressSink | None] = ContextVar("operation_progress", default=None)


@contextmanager
def report_to(sink: ProgressSink) -> Iterator[None]:
    token = observer.set(sink)
    try:
        yield
    finally:
        observer.reset(token)


def stage(message: str, detail: str = "") -> None:
    if sink := observer.get():
        sink(Progress(message, detail))
