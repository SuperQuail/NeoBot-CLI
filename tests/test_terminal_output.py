import threading
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import pytest
from rich.console import Console

from neobot_cli.progress import Progress, observer, stage
from neobot_cli.terminal_output import TerminalOperation


def test_steps_and_output_are_visible_before_operation_finishes():
    output = StringIO()
    operation = TerminalOperation(Console(file=output, force_terminal=False))
    entered, release = threading.Event(), threading.Event()

    def slow():
        stage("准备环境", "正在下载依赖")
        observer.get()(Progress("下载中\n", output=True))
        entered.set()
        assert release.wait(5)
        return {"synced": True}

    with ThreadPoolExecutor() as pool:
        pending = pool.submit(operation.run, "同步环境", slow)
        try:
            assert entered.wait(3)
            assert "准备环境" in output.getvalue()
            assert "下载中" in output.getvalue()
            assert "同步环境完成" not in output.getvalue()
            assert not pending.done()
        finally:
            release.set()
        assert pending.result(timeout=3).ok
    assert "同步环境完成" in output.getvalue()


def test_streaming_chunks_keep_complete_text_and_do_not_interpret_markup():
    output = StringIO()
    terminal = TerminalOperation(Console(file=output, width=120, force_terminal=False))

    def operation():
        observer.get()(Progress("[red]中", output=True))
        observer.get()(Progress("文\n最后一行", output=True))
        return "done"

    assert terminal.run("测试", operation).ok
    text = output.getvalue()
    assert "[red]中文" in text
    assert "最后一行" in text
    assert observer.get() is None


def test_large_partial_lines_are_flushed_in_bounded_chunks():
    output = StringIO()
    terminal = TerminalOperation(Console(file=output, width=120, force_terminal=False))
    terminal._output("x" * 100000)
    assert len(terminal.pending) < 4096
    terminal._flush()
    assert "".join(output.getvalue().split()) == "x" * 100000


def test_keyboard_interrupt_restores_output_and_observer_for_next_operation():
    output = StringIO()
    terminal = TerminalOperation(Console(file=output, force_terminal=True))

    def interrupted():
        observer.get()(Progress("partial", output=True))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        terminal.run("创建工程", interrupted)
    assert observer.get() is None
    assert terminal.run("重试", lambda: "done").ok
    assert "partial" in output.getvalue()
    assert "创建工程已中断" in output.getvalue()
    assert "重试完成" in output.getvalue()
    assert "\x1b[?1049" not in output.getvalue()


@pytest.mark.parametrize("source", ["result", "error", "stage"])
def test_untrusted_text_cannot_control_the_terminal(source):
    from neobot_cli.errors import CliError

    output = StringIO()
    terminal = TerminalOperation(Console(file=output, width=120, force_terminal=False))
    payload = "\x1b[2J\x1b[H[red]中文\x1b]0;标题\x07"

    def operation():
        if source == "error":
            raise CliError("ERROR", payload, hint=payload, details=payload)
        if source == "stage":
            stage(payload, payload)
            return None
        return payload

    terminal.run("读取日志", operation)
    assert "\x1b" not in output.getvalue()
    assert "\x07" not in output.getvalue()
    assert "[red]中文" in output.getvalue()


def test_partial_line_is_visible_while_process_is_still_running(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    output = StringIO()
    terminal = TerminalOperation(Console(file=output, width=140, force_terminal=True))
    entered, release = threading.Event(), threading.Event()

    def slow():
        observer.get()(Progress("Downloading dependency", output=True))
        entered.set()
        assert release.wait(5)
        return None

    with ThreadPoolExecutor() as pool:
        pending = pool.submit(terminal.run, "同步环境", slow)
        try:
            assert entered.wait(3)
            assert "Downloading dependency" in output.getvalue()
            assert not pending.done()
        finally:
            release.set()
        assert pending.result(timeout=3).ok
