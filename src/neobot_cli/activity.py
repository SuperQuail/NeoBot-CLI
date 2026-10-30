"""Bounded, thread-safe handoff from operations to the Textual event loop."""

from collections import deque
from threading import Lock
from time import monotonic

from rich.text import Text

from .progress import Progress


class Activity:
    def __init__(self, label: str):
        self.label = label
        self.stage = label
        self.hint = "操作已开始，可查看实时输出。"
        self.started = self.updated = monotonic()
        self.latest = ""
        self.history: deque[str] = deque(maxlen=1000)
        self._events: deque[Progress] = deque(maxlen=256)
        self._lock = Lock()
        self._dropped = False
        self._latest_stage: Progress | None = None

    def send(self, event: Progress) -> None:
        with self._lock:
            if not event.output:
                self._latest_stage = event
            self._dropped |= len(self._events) == self._events.maxlen
            self._events.append(event)

    def drain(self) -> list[str]:
        with self._lock:
            events = list(self._events)
            dropped = self._dropped
            if dropped and self._latest_stage and self._latest_stage not in events:
                events.insert(0, self._latest_stage)
            self._events.clear()
            self._dropped = False
        lines = ["（输出较多，已省略部分中间内容）"] if dropped else []
        for event in events:
            if event.output:
                clean = Text.from_ansi(event.message).plain
                clean = "".join(c for c in clean if c.isprintable() or c in "\n\r\t")
                if clean.strip():
                    self.latest = clean.splitlines()[-1][-500:]
                    self.updated = monotonic()
                    lines.append(clean.rstrip())
            else:
                self.stage, self.hint = event.message, event.detail
                self.latest = ""
                self.updated = monotonic()
                lines.append(f"\n→ {event.message}\n{event.detail}")
        self.history.extend(lines)
        return lines

    @property
    def elapsed(self) -> str:
        seconds = int(monotonic() - self.started)
        return f"{seconds // 60:02}:{seconds % 60:02}"

    @property
    def heading(self) -> str:
        frame = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int((monotonic() - self.started) * 8) % 10]
        return f"{frame} {self.stage} · {self.elapsed}"

    @property
    def detail(self) -> str:
        silent = int(monotonic() - self.updated)
        if silent >= 15:
            return f"已 {silent} 秒没有新输出，仍在等待进程返回 · F8 查看详情"
        return self.latest or self.hint
