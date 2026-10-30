"""Rich output for one synchronous operation; logs stay in terminal scrollback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from rich.console import Console
from rich.progress import Progress as RichProgress, ProgressColumn, SpinnerColumn, Task, TextColumn, TimeElapsedColumn
from rich.text import Text

from .errors import CliError
from .presentation import format_result
from .progress import Progress, report_to


def clean_text(value: str) -> str:
    plain = Text.from_ansi(value).plain
    return "".join(c for c in plain if c.isprintable() or c in "\n\t")


def literal_text(value: str, style: str = "") -> Text:
    return Text(clean_text(value), style=style)


class WaitingColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        quiet = int(monotonic() - task.fields["updated"])
        return Text(f"已 {quiet} 秒没有新输出，仍在等待" if quiet >= 15 else "", style="dim")


class PartialOutputColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        return Text(task.fields["partial"], overflow="ellipsis", no_wrap=True)


@dataclass(frozen=True)
class Outcome:
    ok: bool
    value: Any = None


class TerminalOperation:
    def __init__(self, console: Console):
        self.console = console
        self.pending = ""

    def _flush(self) -> None:
        if self.pending:
            self.console.print(Text(self.pending))
            self.pending = ""

    def _output(self, text: str) -> None:
        self.pending += clean_text(text)
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.console.print(Text(line))
        # Bound partial lines without buffering the entire command output again.
        while len(self.pending) >= 4096:
            self.console.print(Text(self.pending[:4096]))
            self.pending = self.pending[4096:]

    def run(self, label: str, operation: Callable[[], Any]) -> Outcome:
        self.console.print(literal_text(f"\n{label}", style="bold cyan"))
        started = monotonic()
        try:
            with RichProgress(
                SpinnerColumn(),
                TextColumn("{task.description}", markup=False),
                TimeElapsedColumn(),
                WaitingColumn(),
                PartialOutputColumn(),
                console=self.console,
                transient=True,
                refresh_per_second=4,
            ) as display:
                task = display.add_task(clean_text(label), total=None, updated=monotonic(), partial="")

                def receive(event: Progress) -> None:
                    display.update(task, updated=monotonic())
                    if event.output:
                        self._output(event.message)
                        display.update(task, partial=self.pending, refresh=True)
                    else:
                        self._flush()
                        display.update(task, description=clean_text(event.message), partial="")
                        self.console.print(literal_text(f"→ {event.message}", style="cyan"))
                        if event.detail:
                            self.console.print(literal_text(event.detail, style="dim"))

                try:
                    with report_to(receive):
                        result = operation()
                finally:
                    self._flush()
            if isinstance(result, dict) and result.get("ok") is False:
                self.console.print(literal_text(f"{label}未通过", style="red"))
                self.console.print(literal_text(format_result(result)))
                return Outcome(False, result)
            outcome = "停止请求已发送" if isinstance(result, dict) and result.get("stopping") else f"{label}完成"
            self.console.print(literal_text(f"{outcome} · {monotonic() - started:.1f} 秒", style="green"))
            self.console.print(literal_text(format_result(result)))
            return Outcome(True, result)
        except KeyboardInterrupt:
            self.console.print(literal_text(f"{label}已中断，已生成的文件会保留。", style="yellow"))
            if label == "启动机器人":
                self.console.print("机器人可能仍在启动，可用 neo status 查看。")
            raise
        except Exception as error:
            self.console.print(literal_text(f"{label}失败：{error}", style="red"))
            if isinstance(error, CliError):
                if error.hint:
                    self.console.print(literal_text(error.hint))
                if error.details is not None:
                    self.console.print(literal_text(format_result(error.details)))
            return Outcome(False)
