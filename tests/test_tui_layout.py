"""Viewport checks for the workbench, without scrolling the normal-sized page."""

from __future__ import annotations

import json
import threading

import pytest
from textual.widgets import DataTable, Input, RichLog, Static

from neobot_cli.errors import CliError
from neobot_cli.presentation import format_result
from neobot_cli.tui import WorkbenchApp
from test_tui import FakeWorkbench, wait_until


WORKBENCH_CONTROLS = (
    "#instance-path",
    "#instance-state",
    "#start",
    "#stop",
    "#refresh",
    "#show-logs",
    "#sync-environment",
    "#template",
    "#plugin-name",
    "#create-plugin",
    "#develop-plugin",
    "#check-plugin",
    "#end-development",
    "#plugin-context",
    "#plugin-table",
    "#operation-state",
    "#result-log",
)


def assert_in_viewport(app: WorkbenchApp, *selectors: str) -> None:
    """Check actual geometry; a hidden control or a clipped edge is a failure."""
    for selector in selectors:
        widget = app.query_one(selector)
        assert widget.is_on_screen, selector
        assert widget.region.width > 0 and widget.region.height > 0, (selector, widget.region)
        assert app.screen.region.contains_region(widget.region), (selector, widget.region, app.screen.region)
        clip = app.screen.find_widget(widget).clip
        assert clip.contains_region(widget.region), (selector, widget.region, clip)
        # The footer is fixed to the last line; content may not hide under it.
        assert widget.region.bottom <= app.size.height - 1, (selector, widget.region)


def assert_single_page(app: WorkbenchApp) -> None:
    assert_in_viewport(app, *WORKBENCH_CONTROLS)
    workspace = app.query_one("#workspace")
    assert workspace.max_scroll_y == 0
    assert workspace.max_scroll_x == 0
    assert workspace.scroll_y == 0
    plugin_pane = app.query_one("#plugin-section")
    editor_pane = app.query_one("#plugin-editor")
    result_pane = app.query_one("#result-pane")
    assert_in_viewport(app, "#plugin-section", "#plugin-editor", "#result-pane")
    assert plugin_pane.region.right <= editor_pane.region.x
    assert plugin_pane.region.right <= result_pane.region.x
    assert editor_pane.region.bottom <= result_pane.region.y
    assert app.query_one("#plugin-table", DataTable).region.height >= 4
    assert app.query_one("#result-log", RichLog).region.height >= 5


@pytest.mark.parametrize("size", [(100, 30), (120, 36)])
@pytest.mark.parametrize("running", [False, True])
async def test_selected_instance_keeps_controls_plugins_and_logs_on_one_page(tmp_path, size, running):
    backend = FakeWorkbench(tmp_path / "我的机器人")
    backend.running = running
    backend.plugins = [{"name": "hello", "state": "running", "development": running}]
    app = WorkbenchApp(backend)
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert_single_page(app)
        assert ("正在运行" if running else "尚未运行") in str(app.query_one("#instance-state", Static).render())


@pytest.mark.parametrize("size", [(100, 30), (120, 36)])
async def test_empty_workbench_shows_a_compact_setup_instead_of_disabled_panels(tmp_path, size):
    app = WorkbenchApp(FakeWorkbench(tmp_path, selected=False))
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert_in_viewport(app, "#project-path", "#create-project", "#adopt-project")
        setup = app.query_one("#project-section")
        assert setup.region.width < app.size.width
        assert setup.region.height < app.size.height * 2 / 3
        assert abs(setup.region.x + setup.region.right - app.size.width) <= 2
        assert not app.query_one("#start").is_on_screen
        assert not app.query_one("#template").is_on_screen
        assert not app.query_one("#plugin-table").is_on_screen
        assert app.query_one("#workspace").max_scroll_y == 0


async def test_adopting_an_instance_reveals_the_single_page_without_scrolling(tmp_path):
    backend = FakeWorkbench(tmp_path / "我的机器人", selected=False)
    app = WorkbenchApp(backend)
    async with app.run_test(size=(100, 30)) as pilot:
        app.query_one("#project-path", Input).value = str(backend.root)
        assert await pilot.click("#adopt-project")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert ("adopt_project", str(backend.root)) in backend.calls
        assert_single_page(app)
        assert not app.query_one("#project-actions").is_on_screen


async def test_empty_workbench_shows_validation_and_adoption_progress_beside_the_path(tmp_path):
    backend = FakeWorkbench(tmp_path / "我的机器人", selected=False)
    entered = threading.Event()
    release = threading.Event()
    adopt_project = backend.adopt_project

    def slow_adopt(path):
        entered.set()
        assert release.wait(10)
        return adopt_project(path)

    backend.adopt_project = slow_adopt
    app = WorkbenchApp(backend)
    async with app.run_test(size=(100, 30)) as pilot:
        try:
            assert await pilot.click("#create-project")
            await pilot.pause()
            status = app.query_one("#project-status", Static)
            assert app.last_error
            assert app.last_error in str(status.render())
            assert_in_viewport(app, "#project-status", "#project-path", "#create-project", "#adopt-project")
            assert backend.calls == []

            app.query_one("#project-path", Input).value = str(backend.root)
            await pilot.pause()
            assert not str(status.render()).strip()
            assert backend.calls == []
            assert not entered.is_set()

            assert await pilot.click("#adopt-project")
            await wait_until(entered.is_set)
            assert "接入" in str(status.render())
            assert not app.last_error
            assert backend.project is None
            assert_in_viewport(app, "#project-status")
        finally:
            release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert ("adopt_project", str(backend.root)) in backend.calls
        assert not status.is_on_screen
        assert_single_page(app)


@pytest.mark.parametrize("size", [(48, 20), (32, 20)])
@pytest.mark.parametrize("project_path", [None, r"C:\Users\Neo Bot\我的机器人", r"\\server\Neo Share\我的机器人"])
async def test_failed_adoption_keeps_complete_error_log_visible_and_focusable(
    tmp_path, size, project_path, monkeypatch
):
    backend = FakeWorkbench(tmp_path / "我的机器人", selected=False)
    project_path = project_path or str(backend.root)
    message = "无法接入当前目录，请确认这里包含有效的NeoBot工程配置，修正目录后可以直接重试，无需退出控制台。"
    hint = "检查工程配置文件和当前用户对目录的读取权限。"
    detail = "没有找到pyproject.toml或neobot.toml，请检查目录结构和工程文件。"

    def failed_adopt(path):
        backend.calls.append(("adopt_project", str(path)))
        raise CliError("PROJECT_UNAVAILABLE", message, hint=hint, details={"stderr": detail, "path": str(path)})

    backend.adopt_project = failed_adopt
    app = WorkbenchApp(backend)
    async with app.run_test(size=size) as pilot:
        log = app.query_one("#result-log", RichLog)
        payloads = []

        def record_result(result):
            content = format_result(result)
            if isinstance(result, dict) and "code" in result:
                payloads.append(json.loads(content))
            return content

        # Observe app output once; RichLog may replay deferred writes after layout.
        monkeypatch.setattr("neobot_cli.tui.format_result", record_result)
        app.query_one("#project-path", Input).value = project_path
        assert await pilot.click("#adopt-project")
        await app.workers.wait_for_complete()
        await pilot.press("f8")
        await pilot.pause()
        assert app.focused is log
        assert backend.project is None
        assert app.last_error == message
        assert_in_viewport(app, "#result-pane", "#operation-state", "#result-log")
        assert app.query_one("#workspace").max_scroll_y > 0
        assert log.max_scroll_x == 0
        assert payloads == [
            {
                "code": "PROJECT_UNAVAILABLE",
                "message": message,
                "hint": hint,
                "details": {"stderr": detail, "path": project_path},
            }
        ]
        rendered_text = "".join("".join(line.text.split()) for line in log.lines)
        encoded_path = json.dumps(project_path, ensure_ascii=False)[1:-1]
        for text in (message, hint, detail, "".join(encoded_path.split())):
            assert text in rendered_text


async def test_tiny_header_keeps_version_status_visible_beside_brand(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.status = lambda: {"running": True, "version": "0.3.12", "plugins": []}
    app = WorkbenchApp(backend)
    async with app.run_test(size=(32, 20)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert_in_viewport(app, "#brand", "#instance-state")
        brand = app.query_one("#brand")
        status = app.query_one("#instance-state", Static)
        assert brand.region.right <= status.region.x
        assert "正在运行" in str(status.render())
        assert "正在运行" in str(status.tooltip)
        assert "NeoBot 0.3.12" in str(status.tooltip)


async def test_long_instance_path_does_not_push_workbench_below_the_viewport(tmp_path):
    root = tmp_path.joinpath(*(["很长的实例目录"] * 18), "NeoBot")
    app = WorkbenchApp(FakeWorkbench(root))
    async with app.run_test(size=(100, 30)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert_single_page(app)
        path = app.query_one("#instance-path", Static)
        assert path.region.height <= 2
        assert str(root) in str(path.render()) or str(root) in str(path.tooltip)


async def test_plugin_and_log_scroll_stays_inside_their_panes(tmp_path):
    backend = FakeWorkbench(tmp_path)
    backend.running = True
    backend.plugins = [
        {"name": f"plugin_{index:03}", "state": "running", "development": index % 3 == 0} for index in range(80)
    ]
    backend.logs = lambda lines=100: "\n".join(f"第 {index:03} 行插件日志" for index in range(200))
    app = WorkbenchApp(backend)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        assert await pilot.click("#show-logs")
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#plugin-table", DataTable)
        log = app.query_one("#result-log", RichLog)
        assert table.max_scroll_y > 0
        assert log.max_scroll_y > 0
        table.scroll_end(animate=False)
        log.scroll_end(animate=False)
        await pilot.pause()
        assert table.scroll_y > 0
        assert log.scroll_y > 0
        assert_single_page(app)


async def test_narrow_log_pane_wraps_and_retains_chinese_summary_and_artifact_path(tmp_path):
    backend = FakeWorkbench(tmp_path)
    summary = (
        "插件模板创建完成，已接入当前实例的开发模式，修改源文件后将自动重新加载，完整的操作结果与产物位置都应保留。"
    )
    artifact = (
        "/home/developer/workspaces/NeoBot/plugins/example_plugin/resources/generated/artifacts/plugin-preview.json"
    )
    backend.logs = lambda lines=100: f"{summary}\n{artifact}"
    app = WorkbenchApp(backend)
    async with app.run_test(size=(100, 30)) as pilot:
        await app.workers.wait_for_complete()
        assert await pilot.click("#show-logs")
        await app.workers.wait_for_complete()
        await pilot.pause()
        log = app.query_one("#result-log", RichLog)
        assert log.max_scroll_x == 0
        rendered_text = "".join("".join(line.text.split()) for line in log.lines)
        assert summary in rendered_text
        assert artifact in rendered_text
        assert_single_page(app)


@pytest.mark.parametrize("narrow_size", [(60, 24), (48, 20), (32, 20)])
async def test_resize_to_narrow_and_back_preserves_accessible_controls(tmp_path, narrow_size):
    backend = FakeWorkbench(tmp_path)
    backend.plugins = [{"name": "hello", "state": "running", "development": True}]
    app = WorkbenchApp(backend)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert_single_page(app)
        await pilot.resize_terminal(*narrow_size)
        await pilot.pause()
        plugin = app.query_one("#plugin-section")
        result = app.query_one("#result-pane")
        assert plugin.region.y < result.region.y
        # Scrolling is only allowed for this explicitly narrow fallback.
        for selector in WORKBENCH_CONTROLS:
            app.query_one(selector).scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            assert_in_viewport(app, selector)
        await pilot.resize_terminal(120, 36)
        await pilot.pause()
        assert_single_page(app)
        assert app.query_one("#plugin-table", DataTable).get_row_at(0)[0] == "hello"


@pytest.mark.parametrize("size", [(100, 30), (32, 20)])
async def test_panel_shortcuts_focus_plugins_editor_and_output(tmp_path, size):
    backend = FakeWorkbench(tmp_path)
    backend.plugins = [{"name": "hello", "state": "running", "development": True}]
    app = WorkbenchApp(backend)
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("f6")
        await pilot.pause()
        assert app.focused is app.query_one("#plugin-table", DataTable)
        assert_in_viewport(app, "#plugin-table")

        await pilot.press("f7")
        await pilot.pause()
        editor = app.query_one("#plugin-editor")
        assert app.focused is not None
        assert app.focused.can_focus
        assert app.focused is editor or editor in app.focused.ancestors
        assert app.screen.region.contains_region(app.focused.region)
        assert app.screen.find_widget(app.focused).clip.contains_region(app.focused.region)
        assert app.focused.region.bottom <= app.size.height - 1

        await pilot.press("f8")
        await pilot.pause()
        assert app.focused is app.query_one("#result-log", RichLog)
        assert_in_viewport(app, "#result-log")
        if size[0] >= 96:
            assert_single_page(app)
