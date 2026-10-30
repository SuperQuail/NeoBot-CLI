"""Exercise real child output and failures without downloading a NeoBot instance."""

import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from neobot_cli.errors import CliError
from neobot_cli.progress import observer, report_to, stage
from neobot_cli.project import create_project, discover_project, run_uv
from neobot_cli.workbench import Workbench


def test_output_arrives_before_child_exit_and_both_streams_remain_complete(tmp_path, monkeypatch):
    workspace = create_project(tmp_path / "bot", sync=False)
    monkeypatch.setattr("neobot_cli.project.uv_command", lambda: sys.executable)
    gate = tmp_path / "continue"
    started = threading.Event()
    events = []
    script = (
        "import sys,time,pathlib; "
        "sys.stdout.buffer.write('正在下载'.encode()[:4]); sys.stdout.flush(); "
        "sys.stdout.buffer.write('正在下载'.encode()[4:]); sys.stdout.flush(); "
        "sys.stderr.write('依赖处理中\\n'); sys.stderr.flush(); "
        f"gate=pathlib.Path({str(gate)!r}); "
        "\nwhile not gate.exists(): time.sleep(.01)\n"
        "sys.stdout.write('x'*200000); sys.stderr.write('y'*200000)"
    )

    def receive(event):
        events.append(event)
        if "依赖处理中" in event.message:
            started.set()

    def run():
        with report_to(receive):
            return run_uv(workspace, ["-u", "-c", script], timeout=10)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(run)
        try:
            assert started.wait(5), "output must arrive while the process is still running"
            assert not future.done()
        finally:
            gate.touch()
        result = future.result(timeout=10)
    assert result.returncode == 0
    assert result.stdout == "正在下载" + "x" * 200000
    assert result.stderr == "依赖处理中\n" + "y" * 200000
    assert "正在下载" in "".join(event.message for event in events)
    assert observer.get() is None


def test_child_timeout_stops_process_and_preserves_progress(tmp_path, monkeypatch):
    workspace = create_project(tmp_path / "bot", sync=False)
    monkeypatch.setattr("neobot_cli.project.uv_command", lambda: sys.executable)
    events = []
    with report_to(events.append), pytest.raises(CliError) as error:
        run_uv(workspace, ["-u", "-c", "import time; print('waiting',flush=True); time.sleep(30)"], timeout=1)
    assert error.value.code == "PROCESS_TIMEOUT"
    assert any("waiting" in event.message for event in events)
    assert observer.get() is None


@pytest.mark.parametrize(
    "reason", [CliError("PROCESS_TIMEOUT", "网络等待超时"), CliError("UV_REQUIRED", "找不到 uv"), OSError("启动失败")]
)
def test_creation_retains_project_for_sync_retry_on_launch_errors(tmp_path, monkeypatch, reason):
    monkeypatch.setenv("NEOBOT_CLI_CONFIG_DIR", str(tmp_path / "config"))
    backend = Workbench(load_project=False)

    def fail(*args, **kwargs):
        raise reason

    monkeypatch.setattr("neobot_cli.project.run_uv", fail)
    with pytest.raises(CliError) as error:
        backend.create_project(tmp_path / "bot")
    assert error.value.code == "SYNC_FAILED"
    assert "同步环境" in error.value.hint
    assert backend.project == discover_project(tmp_path / "bot")
    assert Workbench().project == backend.project
    monkeypatch.setattr("neobot_cli.project.run_uv", lambda *args: subprocess.CompletedProcess([], 0, "", ""))
    assert backend.sync()["synced"]


def test_progress_context_does_not_leak_into_cli(capsys):
    stage("outside")
    events = []
    with report_to(events.append):
        stage("inside", "detail")
    stage("outside again")
    assert [event.message for event in events] == ["inside"]
    assert capsys.readouterr() == ("", "")


def test_exited_parent_cannot_leave_ui_waiting_for_inherited_child_output(tmp_path, monkeypatch):
    import time

    workspace = create_project(tmp_path / "bot", sync=False)
    monkeypatch.setattr("neobot_cli.project.uv_command", lambda: sys.executable)
    script = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)']); print('parent done')"
    started = time.monotonic()
    with report_to(lambda event: None):
        result = run_uv(workspace, ["-u", "-c", script], timeout=1)
    assert result.returncode == 0
    assert result.stdout == "parent done\n"
    assert time.monotonic() - started < 1.5


@pytest.mark.parametrize("streamed", [False, True])
def test_captured_text_uses_utf8_and_universal_newlines(tmp_path, monkeypatch, streamed):
    from contextlib import nullcontext

    workspace = create_project(tmp_path / "bot", sync=False)
    monkeypatch.setattr("neobot_cli.project.uv_command", lambda: sys.executable)
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    script = (
        "import sys; sys.stdout.write('中文\\n'); sys.stdout.flush(); "
        "sys.stdout.buffer.write(b'first\\r\\nsecond\\rthird\\n'); "
        "sys.stderr.write('依赖')"
    )
    events = []
    with report_to(events.append) if streamed else nullcontext():
        result = run_uv(workspace, ["-u", "-c", script])
    assert result.returncode == 0, result.stderr
    assert result.stdout == "中文\nfirst\nsecond\nthird\n"
    assert result.stderr == "依赖"
    if streamed:
        assert "中文" in "".join(event.message for event in events)
