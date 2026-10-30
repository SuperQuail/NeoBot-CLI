"""Real Rich prompts driven by terminal transcripts, without full-screen widgets."""

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from neobot_cli.errors import CliError
from neobot_cli.interactive import Menu
from neobot_cli.project import create_project, default_project
from neobot_cli.workbench import Workbench
from fake_workbench import FakeWorkbench


def session(backend, answers, *, width=80):
    output = StringIO()
    console = Console(file=output, width=width, force_terminal=False, color_system=None)
    menu = Menu(backend, console=console, stream=StringIO(answers) if isinstance(answers, str) else answers)
    menu.run()
    return output.getvalue()


def test_create_template_and_develop_use_same_instance_and_exit_keeps_bot(tmp_path):
    backend = FakeWorkbench(tmp_path / "bot", selected=False)
    text = session(backend, f"1\n{backend.root}\n1\n2\nhello\n3\n3\n1\n1\n0\n")
    assert backend.calls == [
        ("create_project", str(backend.root)),
        ("start",),
        ("create_plugin", "hello", "page"),
        ("develop", "hello", True),
    ]
    assert backend.running
    assert backend.plugins[0]["development"]
    assert "创建插件完成" in text
    assert "hello · 本地插件" in text
    assert "已退出菜单" in text


@pytest.mark.parametrize("empty", ["", "   "])
def test_blank_creation_path_uses_visible_home_default(monkeypatch, empty):
    home = Path(Path.cwd().anchor) / "__neo_menu_test_home__"
    monkeypatch.setattr(Path, "home", lambda: home)
    backend = FakeWorkbench(home, selected=False)
    text = session(backend, f"1\n{empty}\n0\n", width=120)
    assert backend.calls == [("create_project", str(home / "NeoBot"))]
    assert str(home / "NeoBot") in text
    assert not (home / "NeoBot").exists()


@pytest.mark.parametrize("width", [32, 80])
def test_long_default_path_wraps_without_losing_text_or_changing_value(tmp_path, width):
    default = str(tmp_path / ("long-home-" * 12) / "NeoBot")
    output = StringIO()
    menu = Menu(
        FakeWorkbench(tmp_path, selected=False),
        console=Console(file=output, width=width, force_terminal=False),
        stream=StringIO("\n"),
    )
    assert menu.ask("实例目录", default=default) == default
    assert default not in output.getvalue()  # This case must actually exercise wrapping.
    assert default in output.getvalue().replace("\n", "")


@pytest.mark.parametrize("answers", ["0\n", "1\n:q\n0\n", "2\n:q\n0\n", ""])
def test_unselected_menu_cancel_and_eof_never_modify_instance(tmp_path, answers):
    backend = FakeWorkbench(tmp_path, selected=False)
    text = session(backend, answers)
    assert not backend.calls
    assert "尚未接入实例" in text
    assert "启动机器人" not in text


@pytest.mark.parametrize("answers", ["2\n:q\n0\n", "2\nhello\n0\n0\n", "4\n0\n0\n"])
def test_backing_out_of_plugin_and_more_menus_has_no_effect(tmp_path, answers):
    backend = FakeWorkbench(tmp_path)
    session(backend, answers)
    assert not backend.calls


def test_invalid_menu_choices_and_empty_name_require_valid_input(tmp_path):
    backend = FakeWorkbench(tmp_path)
    text = session(backend, "unknown\n9\n2\n\n   \nhello\n1\n0\n")
    assert backend.calls == [("create_plugin", "hello", "command")]
    assert "不能为空" in text


def test_failed_adoption_reports_full_error_and_reprompts_without_losing_input(tmp_path):
    backend = FakeWorkbench(tmp_path, selected=False)
    adopt = backend.adopt_project

    def maybe_adopt(path):
        if path == "wrong":
            raise CliError(
                "INVALID_PROJECT", "目录不正确", hint="选择已有工程目录", details={"error": "missing pyproject"}
            )
        return adopt(path)

    backend.adopt_project = maybe_adopt
    text = session(backend, "2\nwrong\ncorrect\n0\n")
    assert backend.calls == [("adopt_project", "correct")]
    assert "目录不正确" in text and "选择已有工程目录" in text and "missing pyproject" in text
    assert "wrong" in text


def test_sync_failure_retains_project_and_can_retry_from_more(tmp_path):
    backend = FakeWorkbench(tmp_path, selected=False)

    def create(path):
        backend.project = SimpleNamespace(root=Path(path))
        raise CliError("SYNC_FAILED", "依赖下载失败", hint="重试同步环境", details={"stderr": "download failed"})

    backend.create_project = create
    text = session(backend, f"1\n{tmp_path}\n4\n2\n0\n")
    assert backend.calls == [("sync",)]
    assert "工程文件已保留" in text
    assert "download failed" in text
    assert "同步环境完成" in text


def test_empty_plugin_menu_does_not_offer_development_actions(tmp_path):
    backend = FakeWorkbench(tmp_path)
    text = session(backend, "3\n0\n")
    assert "还没有本地插件" in text
    assert "开启开发（" not in text
    assert not backend.calls


def test_check_failure_is_not_reported_as_success(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.plugins = [{"name": "hello"}]
    backend.verify_result = {"ok": False, "errors": ["业务测试失败"]}
    text = session(backend, "3\n1\n3\n0\n")
    assert backend.calls == [("verify", "hello")]
    assert "业务测试失败" in text
    assert "检查插件未通过" in text
    assert "检查插件完成" not in text


def test_reload_error_is_visible_and_development_can_be_ended(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.plugins = [
        {"name": "hello", "development": True, "reload": {"status": "failed", "error": "旧版本仍在运行"}}
    ]
    text = session(backend, "3\n1\n2\n0\n")
    assert "hello · 热重载失败" in text
    assert "旧版本仍在运行" in text
    assert backend.calls == [("develop", "hello", False)]


def test_all_more_actions_dispatch_exactly_once(tmp_path):
    backend = FakeWorkbench(tmp_path)
    text = session(backend, "4\n1\n4\n2\n4\n3\n4\n4\n0\n")
    assert backend.calls == [("stop",), ("sync",), ("logs", 100)]
    assert "读取状态完成" in text
    assert "机器人正在运行" in text


def test_quit_and_cancel_do_not_stop_started_bot(tmp_path):
    backend = FakeWorkbench(tmp_path)

    def start():
        backend.calls.append(("start",))
        backend.running = True
        raise KeyboardInterrupt

    backend.start = start
    text = session(backend, "1\n0\n")
    assert backend.running
    assert backend.calls == [("start",)]
    assert "机器人可能仍在启动" in text
    assert "已返回主菜单" in text


def test_ctrl_c_from_a_field_returns_to_menu(tmp_path):
    class InterruptedInput(StringIO):
        def __init__(self):
            super().__init__()
            self.lines = iter(["1\n", KeyboardInterrupt(), "0\n"])

        def readline(self, *args):
            line = next(self.lines)
            if isinstance(line, BaseException):
                raise line
            return line

    backend = FakeWorkbench(tmp_path, selected=False)
    text = session(backend, InterruptedInput())
    assert "已返回主菜单" in text
    assert not backend.calls


@pytest.mark.parametrize("width", [32, 48, 100])
def test_narrow_output_keeps_long_paths_errors_and_literal_markup(tmp_path, width):
    root = tmp_path / ("很长的工程目录" * 8) / "[red]literal"
    backend = FakeWorkbench(root)

    def fail():
        raise CliError("FAILED", "创建失败" * 40, hint="检查当前目录", details={"path": str(root)})

    backend.start = fail
    text = session(backend, "1\n0\n", width=width)
    compact = "".join(text.split())
    assert "".join(str(root).split()) in compact
    assert "创建失败" * 40 in compact
    assert "检查当前目录" in compact
    assert "[red]literal" in compact
    assert "\x1b[?1049" not in text


def test_new_plugin_is_discovered_offline_even_when_sdk_fails(tmp_path, monkeypatch):
    workspace = create_project(tmp_path / "bot", sync=False)
    backend = Workbench(workspace.root)

    def fail(*args):
        raise CliError("SDK_CHECK_FAILED", "SDK 检查未通过")

    monkeypatch.setattr("neobot_cli.sdk.sdk_command", fail)
    text = session(backend, "2\nhello\n1\n3\n0\n0\n")
    assert "SDK 检查未通过" in text
    assert "hello · 本地插件" in text
    assert (workspace.plugin_dir / "hello/plugin.toml").exists()


def test_interrupt_during_initial_sync_keeps_new_project_and_allows_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("NEOBOT_CLI_CONFIG_DIR", str(tmp_path / "config"))
    backend = Workbench(load_project=False)
    calls = []

    def run(*args):
        calls.append(args)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("neobot_cli.project.run_uv", run)
    root = tmp_path / "bot"
    text = session(backend, f"1\n{root}\n4\n2\n0\n")
    assert backend.project.root == root
    assert default_project() == backend.project
    assert len(calls) == 2
    assert "创建工程已中断" in text
    assert "同步环境完成" in text


def test_menu_does_not_poll_instance_in_background(tmp_path):
    backend = FakeWorkbench(tmp_path)
    status_calls = []
    backend.status = lambda: status_calls.append(True) or {"running": False}
    session(backend, "4\n0\n0\n")
    assert status_calls == []


def test_stopping_feedback_blocks_writes_until_next_status_is_offline(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.running = True
    states = iter([True, False])

    def stop():
        backend.calls.append(("stop",))
        return {"running": True, "stopping": True}

    def status():
        stopping = next(states)
        return {"running": stopping, "stopping": stopping}

    backend.stop = stop
    backend.status = status
    text = session(backend, "4\n1\n2\nhello\n1\n2\nhello\n1\n0\n")
    assert backend.calls == [("stop",), ("create_plugin", "hello", "command")]
    assert "停止请求已发送" in text
    assert "停止机器人完成" not in text
    assert "机器人正在停止" in text


def test_cancelled_creation_does_not_adopt_the_parent_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("NEOBOT_CLI_CONFIG_DIR", str(tmp_path / "config"))
    parent = create_project(tmp_path / "parent", sync=False)
    backend = Workbench(load_project=False)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("neobot_cli.project.create_project", interrupt)
    with pytest.raises(KeyboardInterrupt):
        backend.create_project(parent.root / "child")
    assert backend.project is None
    assert default_project() is None


def test_external_menu_fields_are_safe_and_prompt_keeps_original_default():
    from neobot_cli.interactive import Menu

    output = StringIO()
    raw = "\x1b[2J[red]中文"
    console = Console(file=output, force_terminal=False, width=120)
    menu = Menu(FakeWorkbench(Path("/tmp") / raw), console=console, stream=StringIO("\n1\n0\n"))
    assert menu.ask(raw, default=raw) == raw
    assert menu.choose(raw, [(raw, raw)]) == raw
    menu.run()
    assert "\x1b" not in output.getvalue()
    assert "[red]中文" in output.getvalue()
