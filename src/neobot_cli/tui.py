"""The terminal workbench for one NeoBot instance."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.theme import Theme
from textual.widgets import ContentSwitcher, Button, DataTable, Input, Select, Static

from .activity import Activity
from .activity_log import ActivityLog
from .errors import CliError
from .progress import report_to
from .views import compose_workbench
from .presentation import format_result
from .setup_details import SetupDetailsScreen


class WorkbenchBackend(Protocol):
    """UI operations are the same operations exposed by the command line."""

    project: Any

    def templates(self) -> list[dict[str, Any]]: ...
    def create_project(self, path: str | Path) -> dict[str, Any]: ...
    def adopt_project(self, path: str | Path) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...
    def start(self) -> dict[str, Any]: ...
    def stop(self) -> dict[str, Any]: ...
    def sync(self) -> dict[str, Any]: ...
    def create_plugin(self, name: str, template: str) -> dict[str, Any]: ...
    def develop(self, name: str, enabled: bool = True) -> dict[str, Any]: ...
    def verify(self, name: str) -> dict[str, Any]: ...
    def logs(self, lines: int = 100) -> str: ...


class WorkbenchApp(App[None]):
    """Create, use and develop plugins in the same instance."""

    TITLE = "NeoBot"
    SUB_TITLE = "使用与开发"
    BINDINGS = [
        Binding("ctrl+q", "close_workbench", "关闭控制台", priority=True),
        Binding("f4", "show_overview", "概览"),
        Binding("f5", "refresh_instance", "刷新"),
        Binding("f6", "focus_plugins", "插件"),
        Binding("f7", "focus_editor", "开发"),
        Binding("f8", "focus_output", "活动"),
    ]
    CSS_PATH = "workbench.tcss"

    def __init__(self, backend: WorkbenchBackend) -> None:
        super().__init__()
        self.register_theme(
            Theme(
                name="neobot",
                primary="#82c9bd",
                secondary="#8196b4",
                accent="#82c9bd",
                background="#101821",
                surface="#16212d",
                panel="#1e2c3a",
                foreground="#d9e3ed",
                success="#8bc6a4",
                warning="#e0bb80",
                error="#e598a0",
                dark=True,
            )
        )
        self.theme = "neobot"
        self.backend = backend
        self._templates = backend.templates()
        self._busy = False
        self._polling = False
        self._stopping = False
        self._backend_lock = asyncio.Lock()
        self._plugin_rows: tuple[tuple[str, str, str], ...] = ()
        self._poll_error = ""
        self.last_error = ""
        self._setup_result: tuple[str, str] | None = None
        self._activity: Activity | None = None
        self._setup_operation = False
        self._pending_plugin_selection: str | None = None

    @property
    def _view(self):
        # The workbench remains mounted while the live details modal is open.
        return self.screen_stack[0]

    def compose(self) -> ComposeResult:
        yield from compose_workbench(self._templates)

    def on_mount(self) -> None:
        self._view.query_one("#plugin-section").border_title = "插件 · F6"
        self._view.query_one("#plugin-editor").border_title = "工作区 · F7"
        self._view.query_one("#result-pane").border_title = "活动输出 · F8"
        self._view.query_one("#plugin-table", DataTable).add_columns("插件", "状态")
        self._resize_layout()
        self._update_project()
        self._update_template_description()
        self.set_interval(0.1, self._tick_activity)
        self.set_interval(2.0, self._poll_status)
        self._poll_status()

    def _update_project(self) -> None:
        project = self.backend.project
        selected = project is not None
        workspace = self._view.query_one("#workspace")
        workspace.set_class(selected, "has-project")
        workspace.set_class(not selected, "no-project")
        path = self._view.query_one("#instance-path", Static)
        path.update(f"实例  {project.root}" if selected else "")
        path.tooltip = str(project.root) if selected else None
        self._view.query_one("#project-section").display = not selected
        self._view.query_one("#project-path", Input).display = not selected
        self._view.query_one("#project-actions").display = not selected
        self._set_controls()

    def _show_setup_status(self, message: str, *, error: bool = False) -> None:
        status = self._view.query_one("#project-status", Static)
        status.update(message)
        status.tooltip = message or None
        status.set_class(error, "error")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "project-path" and not self._busy:
            self._show_setup_status("")
            self.last_error = ""
            self._setup_result = None
            self._view.query_one("#setup-details").styles.visibility = "hidden"
        elif event.input.id == "plugin-name":
            self._update_plugin_context()

    def on_resize(self, event: events.Resize) -> None:
        if self._view.query("#workspace"):
            self._resize_layout(event.size.width, event.size.height)

    def _resize_layout(self, width: int | None = None, height: int | None = None) -> None:
        workspace = self._view.query_one("#workspace")
        columns = width if width is not None else self.size.width
        rows = height if height is not None else self.size.height
        workspace.set_class(columns < 80, "narrow")
        workspace.set_class(columns < 48, "tiny")
        self._view.query_one("#topbar").set_class(columns < 48, "tiny")
        workspace.set_class(rows < 30, "short")

    def _set_controls(self) -> None:
        selected = self.backend.project is not None
        for widget in self._view.query(Button):
            if widget.id in {
                "nav-overview",
                "nav-plugins",
                "nav-activity",
                "open-activity",
                "go-plugins",
                "setup-details",
            }:
                widget.disabled = False
                continue
            needs_project = widget.id not in {"create-project", "adopt-project", "setup-details"}
            writes_instance = widget.id not in {"refresh", "show-logs", "setup-details"}
            widget.disabled = self._busy or (needs_project and not selected) or (writes_instance and self._stopping)
        for widget in self._view.query(Input):
            widget.disabled = self._busy
        self._view.query_one("#template", Select).disabled = self._busy or not selected

    def _update_template_description(self) -> None:
        chosen = self._view.query_one("#template", Select).value
        item = next((item for item in self._templates if item["id"] == chosen), None)
        self._view.query_one("#template-description", Static).update(item["description"] if item else "")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "template":
            self._update_template_description()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.data_table.get_row(event.row_key)
        self._view.query_one("#plugin-name", Input).value = str(row[0])

    def _update_plugin_context(self) -> None:
        name = self._view.query_one("#plugin-name", Input).value.strip()
        row = next((row for row in self._plugin_rows if row[0] == name), None)
        context = f"{name} · {row[1]}\n{row[2]}" if row else "输入名称创建新插件，或从左侧选择。"
        self._view.query_one("#plugin-context", Static).update(context)

    def _show_status(self, result: dict[str, Any]) -> None:
        running = bool(result.get("running", result.get("instance_id")))
        self._stopping = bool(result.get("stopping", False))
        state = "正在停止" if self._stopping else ("正在运行" if running else "尚未运行")
        if result.get("version"):
            state += f" · NeoBot {result['version']}"
        self._view.query_one("#instance-state", Static).update(state)
        self._view.query_one("#runtime-summary", Static).update(state)
        self._view.query_one("#runtime-hint", Static).update(
            "运行中 · 插件页可开启开发和热重载。" if running else "启动后即可连接平台与使用插件。"
        )
        self._view.query_one("#instance-state").tooltip = state
        self._view.query_one("#instance-state").set_class(running and not self._stopping, "running")
        self._view.query_one("#instance-state").set_class(self._stopping, "stopping")
        self._set_controls()
        if "plugins" not in result:
            return
        rows = []
        for plugin in result.get("plugins", []):
            reload = plugin.get("reload") or {}
            development = "开发中" if plugin.get("development") else "—"
            if reload.get("status") and reload["status"] != "idle":
                development += f" · {reload['status']}"
                if reload.get("error"):
                    development += f"：{reload['error']}"
            rows.append((str(plugin["name"]), str(plugin.get("state", "unknown")), development))
        values = tuple(rows)
        self._view.query_one("#plugin-empty").display = not values
        if values != self._plugin_rows:
            table = self._view.query_one("#plugin-table", DataTable)
            selected_name = str(table.get_row_at(table.cursor_row)[0]) if table.row_count else ""
            table.clear()
            old_rows = {row[0]: row for row in self._plugin_rows}
            plugins = {str(plugin["name"]): plugin for plugin in result["plugins"]}
            for name, state, development in values:
                reload_failed = (plugins[name].get("reload") or {}).get("status") == "failed"
                failed = reload_failed or state in {"failed", "error"}
                developing = bool(plugins[name].get("development"))
                badge = "异常" if failed else ("开发" if developing else ("运行" if state == "running" else "未运行"))
                color = (
                    "error"
                    if failed
                    else ("primary" if developing else ("success" if state == "running" else "foreground"))
                )
                table.add_row(name, Text(badge, style=getattr(self.current_theme, color)), key=name)
                if name == selected_name:
                    table.move_cursor(row=table.row_count - 1)
                if reload_failed and old_rows.get(name) != (name, state, development):
                    self._write_result(f"{name} · 热重载失败", development)
            self._plugin_rows = values
        if self._pending_plugin_selection:
            names = [row[0] for row in values]
            if self._pending_plugin_selection in names:
                self._view.query_one("#plugin-table", DataTable).move_cursor(
                    row=names.index(self._pending_plugin_selection)
                )
                self._pending_plugin_selection = None
        developing = sum(row[2].startswith("开发中") for row in values)
        self._view.query_one("#plugin-summary", Static).update(f"{len(values)} 个插件 · {developing} 开发中")
        self._view.query_one("#overview-plugins", Static).update(f"{len(values)} 个插件 · {developing} 开发中")
        self._update_plugin_context()

    def _write_result(self, label: str, result: Any) -> None:
        if self.backend.project is None:
            trace = "\n".join(self._activity.history) + "\n\n" if self._activity else ""
            self._setup_result = (label, trace + format_result(result))
            if isinstance(self.screen, SetupDetailsScreen):
                self.screen.append_content(format_result(result), title=label)
            self._view.query_one("#setup-details").styles.visibility = "visible"
            return
        self._view.query_one("#workspace").add_class("has-output")
        self._view.query_one("#result-empty").display = False
        self._append_result(label, result)
        if isinstance(self.screen, SetupDetailsScreen):
            self.screen.append_content(format_result(result), title=label)

    def _append_result(self, label: str, result: Any) -> None:
        log = self._view.query_one("#result-log", ActivityLog)
        log.append(f"{label}\n{format_result(result)}\n")

    @staticmethod
    def _error_details(error: Exception) -> dict[str, Any]:
        return error.to_dict() if isinstance(error, CliError) else {"message": str(error)}

    @work(group="poll-status")
    async def _poll_status(self) -> None:
        if self._busy or self._polling or self.backend.project is None:
            return
        self._polling = True
        try:
            async with self._backend_lock:
                result = await asyncio.to_thread(self.backend.status)
            if not self._view.query("#instance-state"):
                return
            self._show_status(result)
            self._poll_error = ""
        except Exception as exc:
            if not self._view.query("#instance-state"):
                return
            message = str(exc)
            self._view.query_one("#instance-state", Static).update("暂时无法读取实例状态")
            if message != self._poll_error:
                self._write_result("读取状态失败", self._error_details(exc))
                self._poll_error = message
        finally:
            self._polling = False

    def _submit(self, label: str, operation: Callable[[], Any]) -> None:
        if self._busy:
            return
        if self._stopping and label not in {"刷新状态", "读取日志"}:
            self.last_error = "机器人正在停止，请等待停止完成后再操作"
            self._view.query_one("#operation-state", Static).update(self.last_error)
            return
        self._busy = True
        self._setup_operation = self.backend.project is None
        self._activity = Activity(label)
        self.last_error = ""
        self._view.query_one("#operation-state", Static).update(f"{label}…")
        if self.backend.project is None:
            self._show_setup_status(f"{label}…")
        if self._setup_operation:
            self._setup_result = (label, "操作已开始。")
            self._view.query_one("#setup-details").styles.visibility = "visible"
        self._view.query_one("#operation-state").remove_class("error")
        self._tick_activity()
        self._set_controls()
        self._perform(label, operation)

    @work(group="operation")
    async def _perform(self, label: str, operation: Callable[[], Any]) -> None:
        try:
            async with self._backend_lock:

                def observed():
                    with report_to(self._activity.send):
                        return operation()

                result = await asyncio.to_thread(observed)
            self._tick_activity()
            if isinstance(result, dict) and result.get("ok") is False:
                self.last_error = str(result.get("error") or result.get("errors") or "操作未通过")
                self._view.query_one("#operation-state", Static).update(f"{label}未通过")
                self._view.query_one("#operation-state").add_class("error")
            else:
                if isinstance(result, dict) and any(key in result for key in ("running", "instance_id", "stopping")):
                    self._show_status(result)
                stopping = isinstance(result, dict) and bool(result.get("stopping"))
                outcome = "机器人正在停止，完成后可继续操作" if stopping else f"{label}完成 · {self._activity.elapsed}"
                self._view.query_one("#operation-state", Static).update(outcome)
            self._write_result(label, result)
            self._view.query_one("#operation-detail", Static).update(
                self.last_error
                or (
                    "下一步：启动机器人，或进入插件页创建功能。"
                    if self._setup_operation
                    else "可继续操作，完整结果见活动页。"
                )
            )
        except Exception as exc:
            self._tick_activity()
            self.last_error = str(exc)
            self._view.query_one("#operation-state", Static).update(f"{label}失败 · {self._activity.elapsed}")
            self._view.query_one("#operation-state").add_class("error")
            self._view.query_one("#operation-detail", Static).update(
                str(exc) + (f" · {exc.hint}" if isinstance(exc, CliError) and exc.hint else "")
            )
            if self.backend.project is None:
                message = str(exc)
                if isinstance(exc, CliError) and exc.hint:
                    message += f"\n{exc.hint}"
                self._show_setup_status(message, error=True)
            self._write_result(f"{label}失败", self._error_details(exc))
        finally:
            self._busy = False
            if isinstance(self.screen, SetupDetailsScreen) and self.backend.project is not None:
                self.screen.append_content(
                    str(self._view.query_one("#operation-state", Static).render()), title=f"{label}结束"
                )
            self._update_project()
            self._poll_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        navigation = {
            "nav-overview": "overview",
            "nav-plugins": "plugins",
            "go-plugins": "plugins",
            "nav-activity": "activity",
            "open-activity": "activity",
        }
        if event.button.id in navigation:
            self._show_page(navigation[event.button.id])
            return
        if event.button.id == "setup-details":
            self._show_setup_details()
            return
        name = self._view.query_one("#plugin-name", Input).value.strip()
        path = self._view.query_one("#project-path", Input).value.strip()
        if event.button.id == "create-project" and not path:
            path = str(Path.home() / "NeoBot")
            self._view.query_one("#project-path", Input).value = path
        template = str(self._view.query_one("#template", Select).value)
        actions: dict[str, tuple[str, Callable[[], Any]]] = {
            "create-project": ("创建工程", lambda: self.backend.create_project(path)),
            "adopt-project": ("接入工程", lambda: self.backend.adopt_project(path)),
            "start": ("启动机器人", self.backend.start),
            "stop": ("停止机器人", self.backend.stop),
            "refresh": ("刷新状态", self.backend.status),
            "show-logs": ("读取日志", self.backend.logs),
            "sync-environment": ("同步环境", self.backend.sync),
            "create-plugin": ("创建插件", lambda: self.backend.create_plugin(name, template)),
            "develop-plugin": ("开启开发", lambda: self.backend.develop(name)),
            "check-plugin": ("检查插件", lambda: self.backend.verify(name)),
            "end-development": ("结束开发", lambda: self.backend.develop(name, enabled=False)),
        }
        if event.button.id in actions:
            label, action = actions[event.button.id]
            if event.button.id in {"create-project", "adopt-project"} and not path:
                self.last_error = "请先填写实例目录"
                self._view.query_one("#operation-state", Static).update(self.last_error)
                self._show_setup_status(self.last_error, error=True)
                self._view.query_one("#project-path", Input).focus()
                return
            if event.button.id in {"create-plugin", "develop-plugin", "check-plugin", "end-development"} and not name:
                self.last_error = "请先填写或选择插件名称"
                self._view.query_one("#operation-state", Static).update(self.last_error)
                self._view.query_one("#plugin-name", Input).focus()
                return
            if event.button.id == "create-plugin" and not self._busy:
                self._pending_plugin_selection = name
            if event.button.id == "show-logs":
                self._show_page("activity")
            self._submit(label, action)

    def _show_page(self, page: str) -> None:
        if self.backend.project is None:
            return
        if page != "activity":
            self._view.query_one("#result-log", ActivityLog).pause_output()
        self._view.query_one("#pages", ContentSwitcher).current = f"{page}-page"
        for button in self._view.query(".nav"):
            button.set_class(button.id == f"nav-{page}", "active")

    def action_show_overview(self) -> None:
        if self.screen is not self._view:
            return
        self._show_page("overview")
        if self.backend.project is not None:
            self._view.query_one("#nav-overview").focus()

    def _tick_activity(self) -> None:
        if not self._busy or self._activity is None:
            return
        lines = self._activity.drain()
        self._view.query_one("#operation-state", Static).update(self._activity.heading)
        self._view.query_one("#operation-detail", Static).update(self._activity.detail)
        self._view.query_one("#operation-detail").tooltip = self._activity.detail
        if self._setup_operation:
            self._show_setup_status(f"{self._activity.heading}\n{self._activity.detail}")
            self._setup_result = (self._activity.label, "\n".join(self._activity.history) or self._activity.hint)
            if isinstance(self.screen, SetupDetailsScreen):
                self.screen.set_progress(self._activity.heading)
                if lines:
                    self.screen.append_content("\n".join(lines))
        if lines:
            self._view.query_one("#result-empty").display = False
            log = self._view.query_one("#result-log", ActivityLog)
            log.append("\n".join(lines))

    def action_refresh_instance(self) -> None:
        self._poll_status()

    def action_focus_plugins(self) -> None:
        if self.backend.project is not None and self.screen is self._view:
            self._show_page("plugins")
            self._focus_panel("#plugin-table", "#plugin-section")

    def action_focus_editor(self) -> None:
        if self.backend.project is not None and self.screen is self._view:
            self._show_page("plugins")
            self._focus_panel("#plugin-name", "#plugin-editor")

    def action_focus_output(self) -> None:
        if self.screen is not self._view:
            return
        if self._view.query_one("#workspace").has_class("no-project"):
            self._show_setup_details()
        else:
            self._show_page("activity")
            self._focus_panel("#result-log", "#result-pane")

    def _show_setup_details(self) -> None:
        if self._setup_result is not None and not isinstance(self.screen, SetupDetailsScreen):
            self.push_screen(
                SetupDetailsScreen(*self._setup_result),
                lambda _: self._view.query_one(
                    "#project-path" if self.backend.project is None else "#nav-overview"
                ).focus(scroll_visible=False),
            )

    def _focus_panel(self, control: str, panel: str) -> None:
        self._view.query_one(control).focus(scroll_visible=False)
        self.call_after_refresh(self._view.query_one(control).scroll_visible, animate=False, immediate=True)

    def action_close_workbench(self) -> None:
        if self._busy:
            self.notify("当前操作尚未完成，请稍候再关闭控制台")
            return
        self.exit()


def run_tui(backend: WorkbenchBackend) -> None:
    WorkbenchApp(backend).run()
