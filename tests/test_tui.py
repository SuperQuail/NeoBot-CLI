from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Button, DataTable, Input, RichLog, Select, Static

from neobot_cli.errors import CliError
from neobot_cli.tui import WorkbenchApp


class FakeWorkbench:
    def __init__(self, root: Path, *, selected: bool = True) -> None:
        self.root = root
        self.project = SimpleNamespace(root=root) if selected else None
        self.calls: list[tuple] = []
        self.running = False
        self.stopping = False
        self.plugins: list[dict] = []
        self.start_entered = threading.Event()
        self.start_release: threading.Event | None = None
        self.verify_result = {"ok": True, "checks": ["business tests"]}

    def templates(self):
        return [
            {"id": "command", "title": "聊天命令", "description": "处理聊天命令"},
            {"id": "event", "title": "定时与事件", "description": "处理后台事件"},
            {"id": "page", "title": "带自定义页面", "description": "在浏览器打开插件页面"},
        ]

    def create_project(self, path):
        self.calls.append(("create_project", str(path)))
        self.project = SimpleNamespace(root=Path(path))
        return {"path": str(path)}

    def adopt_project(self, path):
        self.calls.append(("adopt_project", str(path)))
        self.project = SimpleNamespace(root=Path(path))
        return {"path": str(path)}

    def status(self):
        return {"running": self.running, "stopping": self.stopping, "plugins": self.plugins}

    def start(self):
        self.calls.append(("start",))
        self.start_entered.set()
        if self.start_release is not None:
            assert self.start_release.wait(5)
        self.running = True
        return self.status()

    def stop(self):
        self.calls.append(("stop",))
        self.running = False
        return self.status()

    def sync(self):
        self.calls.append(("sync",))
        return {"synced": True, "requirements": ["httpx>=0.27"]}

    def create_plugin(self, name, template):
        self.calls.append(("create_plugin", name, template))
        self.plugins.append({"name": name, "state": "unloaded", "development": False})
        return {"name": name, "template": template}

    def develop(self, name, enabled=True):
        self.calls.append(("develop", name, enabled))
        for plugin in self.plugins:
            if plugin["name"] == name:
                plugin.update(state="running", development=enabled)
        return self.status()

    def verify(self, name):
        self.calls.append(("verify", name))
        return self.verify_result

    def logs(self, lines=100):
        self.calls.append(("logs", lines))
        return "机器人正在运行"


async def wait_until(predicate) -> None:
    async with asyncio.timeout(4):
        while not predicate():
            await asyncio.sleep(0.01)


async def click_visible(app, pilot, selector):
    app.query_one(selector).scroll_visible(animate=False, immediate=True)
    await pilot.pause()
    assert await pilot.click(selector)
    await pilot.pause()


@pytest.mark.asyncio
async def test_create_choose_template_and_develop_same_instance(tmp_path):
    backend = FakeWorkbench(tmp_path / "我的机器人", selected=False)
    app = WorkbenchApp(backend)
    async with app.run_test(size=(90, 35)) as pilot:
        app.query_one("#project-path", Input).value = str(backend.root)
        await click_visible(app, pilot, "#create-project")
        await app.workers.wait_for_complete()
        assert not app.query_one("#project-actions").display
        assert str(backend.root) in str(app.query_one("#instance-path", Static).render())
        await click_visible(app, pilot, "#start")
        await app.workers.wait_for_complete()
        template = app.query_one("#template", Select)
        template.scroll_visible(animate=False, immediate=True)
        template.focus()
        await pilot.press("enter", "down", "down", "enter")
        assert template.value == "page"
        app.query_one("#plugin-name", Input).value = "hello"
        await click_visible(app, pilot, "#create-plugin")
        await app.workers.wait_for_complete()
        await click_visible(app, pilot, "#develop-plugin")
        await app.workers.wait_for_complete()
        assert ("create_plugin", "hello", "page") in backend.calls
        assert ("develop", "hello", True) in backend.calls
        assert sum(call[0] == "start" for call in backend.calls) == 1
        assert backend.plugins[0]["development"] is True
        await pilot.press("ctrl+q")
    assert ("stop",) not in backend.calls


@pytest.mark.asyncio
async def test_failed_checks_are_visible_and_do_not_report_completion(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.verify_result = {"ok": False, "errors": ["测试断言失败"]}
    app = WorkbenchApp(backend)
    async with app.run_test() as pilot:
        app.query_one("#plugin-name", Input).value = "broken"
        await click_visible(app, pilot, "#check-plugin")
        await app.workers.wait_for_complete()
        assert "测试断言失败" in app.last_error
        assert "未通过" in str(app.query_one("#operation-state", Static).render())
        assert not app.query_one("#check-plugin", Button).disabled


@pytest.mark.asyncio
async def test_slow_start_keeps_ui_responsive_and_prevents_duplicate_operations(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.start_release = threading.Event()
    app = WorkbenchApp(backend)
    async with app.run_test() as pilot:
        try:
            await click_visible(app, pilot, "#start")
            await wait_until(backend.start_entered.is_set)
            assert app.query_one("#start", Button).disabled
            assert app.query_one("#stop", Button).disabled
            await pilot.press("f5")
            await pilot.resize_terminal(64, 24)
            assert app.size.width == 64
            assert app.is_running
            assert backend.calls == [("start",)]
        finally:
            backend.start_release.set()
        await app.workers.wait_for_complete()
        assert backend.running
        assert not app.query_one("#start", Button).disabled


@pytest.mark.asyncio
async def test_empty_fields_never_call_backend_and_small_terminal_can_reach_actions(tmp_path):
    backend = FakeWorkbench(tmp_path, selected=False)
    app = WorkbenchApp(backend)
    async with app.run_test(size=(48, 20)) as pilot:
        await click_visible(app, pilot, "#create-project")
        assert app.last_error == "请先填写实例目录"
        assert not backend.calls
        app.query_one("#project-path", Input).value = str(tmp_path)
        await click_visible(app, pilot, "#adopt-project")
        await app.workers.wait_for_complete()
        await click_visible(app, pilot, "#develop-plugin")
        assert app.last_error == "请先填写或选择插件名称"
        assert backend.calls == [("adopt_project", str(tmp_path))]


@pytest.mark.asyncio
async def test_backend_exception_is_reported_and_controls_recover(tmp_path):
    backend = FakeWorkbench(tmp_path)

    def fail():
        raise RuntimeError("当前实例已在别的终端运行")

    backend.start = fail
    app = WorkbenchApp(backend)
    async with app.run_test() as pilot:
        await click_visible(app, pilot, "#start")
        await app.workers.wait_for_complete()
        assert app.last_error == "当前实例已在别的终端运行"
        assert "失败" in str(app.query_one("#operation-state", Static).render())
        assert not app.query_one("#start", Button).disabled


@pytest.mark.asyncio
async def test_reload_failure_is_visible_and_plugin_can_be_selected(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.running = True
    backend.plugins = [{"name": "hello", "state": "running", "development": True, "reload": {"status": "success"}}]
    app = WorkbenchApp(backend)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        table = app.query_one("#plugin-table", DataTable)
        table.scroll_visible(animate=False, immediate=True)
        table.focus()
        await pilot.press("enter")
        assert app.query_one("#plugin-name", Input).value == "hello"
        backend.plugins[0]["reload"] = {"status": "failed", "error": "源码导入失败，旧版本继续运行"}
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        assert table.get_row_at(0) == ["hello", "running", "开发中 · failed：源码导入失败，旧版本继续运行"]
        assert "正在运行" in str(app.query_one("#instance-state", Static).render())


@pytest.mark.asyncio
async def test_creation_sync_failure_retains_project_and_can_retry_sync(tmp_path):
    backend = FakeWorkbench(tmp_path, selected=False)

    def create_with_download_failure(path):
        backend.calls.append(("create_project", str(path)))
        backend.project = SimpleNamespace(root=Path(path))
        raise CliError(
            "SYNC_FAILED",
            "工程已创建，依赖下载失败",
            hint="检查网络后点击同步环境",
            details={"stderr": "无法连接包源，请检查代理设置"},
        )

    backend.create_project = create_with_download_failure
    app = WorkbenchApp(backend)
    async with app.run_test(size=(90, 35)) as pilot:
        app.query_one("#project-path", Input).value = str(tmp_path)
        await click_visible(app, pilot, "#create-project")
        await app.workers.wait_for_complete()
        assert app.last_error == "工程已创建，依赖下载失败"
        assert not app.query_one("#project-actions").display
        log = app.query_one("#result-log", RichLog)
        visible_log = "\n".join(line.text for line in log.lines)
        assert "检查网络后点击同步环境" in visible_log
        assert "无法连接包源，请检查代理设置" in visible_log
        await click_visible(app, pilot, "#sync-environment")
        await app.workers.wait_for_complete()
        assert backend.calls == [("create_project", str(tmp_path)), ("sync",)]
        assert not app.last_error
        assert "同步环境完成" in str(app.query_one("#operation-state", Static).render())


@pytest.mark.asyncio
async def test_stopping_is_visible_and_blocks_writes_until_offline(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.running = True

    def request_stop():
        backend.calls.append(("stop",))
        backend.stopping = True
        return {"instance_id": "current-instance", "stopping": True}

    backend.stop = request_stop
    app = WorkbenchApp(backend)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await click_visible(app, pilot, "#stop")
        await app.workers.wait_for_complete()
        assert "正在停止" in str(app.query_one("#instance-state", Static).render())
        assert "停止机器人完成" not in str(app.query_one("#operation-state", Static).render())
        for identifier in ("start", "stop", "create-plugin", "develop-plugin", "check-plugin", "sync-environment"):
            assert app.query_one(f"#{identifier}", Button).disabled
        assert not app.query_one("#refresh", Button).disabled
        assert not app.query_one("#show-logs", Button).disabled
        backend.running = False
        backend.stopping = False
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        assert "尚未运行" in str(app.query_one("#instance-state", Static).render())
        assert not app.query_one("#start", Button).disabled
        assert not app.query_one("#sync-environment", Button).disabled
