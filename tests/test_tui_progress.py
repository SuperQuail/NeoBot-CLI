import threading
from types import SimpleNamespace

import pytest
from textual.widgets import Button, ContentSwitcher, DataTable, Input, RichLog, Static

from neobot_cli.errors import CliError
from neobot_cli.progress import Progress, observer, stage
from neobot_cli.setup_details import SetupDetailsScreen
from neobot_cli.tui import WorkbenchApp
from test_tui import FakeWorkbench, wait_until


def contents(log):
    return "\n".join(line.text for line in log.lines)


@pytest.mark.parametrize("finish", ["success", "download_failure", "validation_failure"])
async def test_slow_creation_has_live_details_timer_and_safe_completion(tmp_path, finish):
    backend = FakeWorkbench(tmp_path, selected=False)
    entered, proceed, end = threading.Event(), threading.Event(), threading.Event()

    def create(path):
        stage("3/3 · 准备环境", "首次需要下载宿主和依赖。")
        observer.get()(Progress("Downloading dependency-a\n", output=True))
        entered.set()
        assert proceed.wait(10)
        observer.get()(Progress("Downloaded dependency-a\n", output=True))
        assert end.wait(10)
        if finish != "validation_failure":
            backend.project = SimpleNamespace(root=tmp_path)
        if finish != "success":
            raise CliError(
                "SYNC_FAILED", "模拟网络失败", hint="点击同步环境重试", details={"stderr": "download failed"}
            )
        return {"root": str(tmp_path)}

    backend.create_project = create
    app = WorkbenchApp(backend)
    async with app.run_test(size=(100, 30)) as pilot:
        geometry = app.query_one("#project-actions").region
        try:
            await pilot.click("#create-project")
            await wait_until(entered.is_set)
            await pilot.pause()
            status = app.query_one("#project-status", Static)
            assert "准备环境" in str(status.render())
            assert "Downloading dependency-a" in str(status.render())
            assert app.query_one("#project-actions").region == geometry
            assert app.query_one("#create-project", Button).disabled
            assert not app.query_one("#setup-details", Button).disabled
            app._activity.started -= 65
            app._tick_activity()
            assert "01:05" in str(status.render())
            await pilot.press("f8")
            await pilot.pause()
            assert isinstance(app.screen, SetupDetailsScreen)
            screen = app.screen
            log = screen.query_one("#setup-details-log", RichLog)
            assert "Downloading dependency-a" in contents(log)
            proceed.set()
            await wait_until(lambda: "Downloaded dependency-a" in contents(log))
            await pilot.press("f8")
            assert app.screen is screen
            end.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app._busy
            assert "结束" in str(screen.query_one("#setup-details-title", Static).render()) or "失败" in str(
                screen.query_one("#setup-details-title", Static).render()
            )
            if finish != "success":
                assert "download failed" in contents(log)
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is app._view
            if finish == "validation_failure":
                assert app.focused is app.query_one("#project-path")
                assert app.query_one("#project-actions").region == geometry
            else:
                assert app.focused is app.query_one("#nav-overview")
                assert not app.query_one("#sync-environment", Button).disabled
        finally:
            proceed.set()
            end.set()


async def test_busy_operation_allows_page_navigation_and_keeps_form_values(tmp_path):
    backend = FakeWorkbench(tmp_path)
    release, entered = threading.Event(), threading.Event()

    def sync():
        stage("同步 Python 环境")
        observer.get()(Progress("Downloading package\n", output=True))
        entered.set()
        assert release.wait(10)
        return {"synced": True}

    backend.sync = sync
    app = WorkbenchApp(backend)
    async with app.run_test(size=(100, 30)) as pilot:
        app.query_one("#plugin-name", Input).value = "my_plugin"
        try:
            await pilot.click("#sync-environment")
            await wait_until(entered.is_set)
            await pilot.click("#nav-activity")
            await pilot.pause()
            assert "Downloading package" in contents(app.query_one("#result-log", RichLog))
            await pilot.press("f7")
            assert app.query_one("#pages", ContentSwitcher).current == "plugins-page"
            assert app.query_one("#plugin-name", Input).value == "my_plugin"
            assert app.query_one("#create-plugin", Button).disabled
            assert not app.query_one("#nav-overview", Button).disabled
        finally:
            release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one("#pages", ContentSwitcher).current == "plugins-page"
        assert not app.query_one("#create-plugin", Button).disabled
        assert "同步环境完成" in str(app.query_one("#operation-state", Static).render())


async def test_creating_plugin_updates_list_immediately_and_refresh_preserves_selection(tmp_path, monkeypatch):
    from neobot_cli.project import create_project
    from neobot_cli.workbench import Workbench

    workspace = create_project(tmp_path / "bot", sync=False)
    backend = Workbench(workspace.root)
    monkeypatch.setattr("neobot_cli.sdk.sdk_command", lambda *args: {"ok": True})
    app = WorkbenchApp(backend)
    async with app.run_test(size=(100, 30)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("f7")
        app.query_one("#plugin-name", Input).value = "z_plugin"
        await pilot.click("#create-plugin")
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#plugin-table", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "z_plugin"
        assert not app.query_one("#plugin-empty").display
        table.focus()
        await pilot.press("enter")
        assert app.query_one("#plugin-name", Input).value == "z_plugin"
        backend.create_plugin("a_plugin", "command")
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert table.row_count == 2
        assert table.get_row_at(table.cursor_row)[0] == "z_plugin"
        assert app.query_one("#plugin-name", Input).value == "z_plugin"
        app.query_one("#plugin-name", Input).value = "zz_new_plugin"
        await pilot.click("#create-plugin")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert table.get_row_at(table.cursor_row)[0] == "zz_new_plugin"


async def test_live_output_keeps_reading_position_until_user_returns_to_bottom(tmp_path):
    app = WorkbenchApp(FakeWorkbench(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("f8")
        app._write_result("历史", "\n".join(f"第 {i} 行" for i in range(100)))
        await pilot.pause()
        log = app.query_one("#result-log", RichLog)
        log.scroll_home(animate=False)
        await pilot.pause()
        app._write_result("新输出", "不要跳到这里")
        await pilot.pause()
        assert log.scroll_y == 0
        log.scroll_end(animate=False)
        await pilot.pause()
        app._write_result("新输出", "现在跟随")
        await pilot.pause()
        assert log.is_vertical_scroll_end


async def test_output_written_while_page_is_hidden_keeps_lines_and_reading_position(tmp_path):
    app = WorkbenchApp(FakeWorkbench(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("f8")
        app._write_result("历史", "\n".join(f"line {i} is readable" for i in range(100)))
        await pilot.pause()
        log = app.query_one("#result-log", RichLog)
        log.scroll_to(y=10, animate=False, immediate=True)
        await pilot.pause()
        previous = log.scroll_y
        await pilot.press("f6")
        await pilot.pause()
        app._write_result("新增", "Downloading dependency-alpha 123")
        await pilot.press("f8")
        await pilot.pause()
        assert "Downloading dependency-alpha 123" in contents(log)
        assert log.scroll_y == previous
        assert len(log.lines) < 120
        log.scroll_end(animate=False)
        await pilot.pause()
        await pilot.press("f4")
        app._write_result("新增", "Downloaded dependency-alpha 123")
        await pilot.press("f8")
        await pilot.pause()
        assert "Downloaded dependency-alpha 123" in contents(log)
        assert log.is_vertical_scroll_end
