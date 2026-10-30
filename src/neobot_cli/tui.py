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
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Button, DataTable, Footer, Input, Label, RichLog, Select, Static

from .errors import CliError
from .presentation import format_result


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
        Binding("f5", "refresh_instance", "刷新"),
        Binding("f6", "focus_plugins", "插件"),
        Binding("f7", "focus_editor", "开发"),
        Binding("f8", "focus_output", "输出"),
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

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Label("NeoBot", id="brand")
            yield Static("使用与开发", id="tagline")
            yield Static("未接入实例", id="instance-state", markup=False)
        with VerticalScroll(id="workspace", classes="no-project"):
            with Vertical(id="project-section"):
                yield Label("接入你的 NeoBot", id="project-heading")
                yield Static("选择已有工程，或在新目录创建。", id="project-hint", markup=False)
                yield Static(id="instance-path", markup=False)
                yield Input(placeholder="例如 ~/NeoBot，创建时请选择新目录", id="project-path")
                yield Static(id="project-status", markup=False)
                with Horizontal(id="project-actions"):
                    yield Button("接入已有工程", id="adopt-project", classes="compact primary")
                    yield Button("创建新工程", id="create-project", classes="compact")
            with Horizontal(id="instance-actions"):
                yield Button("启动", id="start", classes="compact primary")
                yield Button("停止", id="stop", classes="compact")
                yield Button("同步环境", id="sync-environment", classes="compact")
                yield Button("刷新", id="refresh", classes="compact")
                yield Button("查看日志", id="show-logs", classes="compact")
            with Horizontal(id="workbench-body"):
                with Vertical(id="plugin-section"):
                    yield Static("本地插件", id="plugin-summary", markup=False)
                    yield Static("还没有插件。\n在工作区创建第一个。", id="plugin-empty", markup=False)
                    yield DataTable(id="plugin-table", cursor_type="row", show_header=False)
                with Vertical(id="detail-column"):
                    with Vertical(id="plugin-editor"):
                        yield Static("输入名称创建新插件，或从左侧选择。", id="plugin-context", markup=False)
                        with Horizontal(id="plugin-form"):
                            with Vertical(classes="field", id="name-field"):
                                yield Label("插件名称")
                                yield Input(placeholder="例如 hello", id="plugin-name")
                            with Vertical(classes="field"):
                                yield Label("新建模板")
                                yield Select(
                                    [(item["title"], item["id"]) for item in self._templates],
                                    allow_blank=False,
                                    id="template",
                                )
                        yield Static(id="template-description", markup=False)
                        with Horizontal(id="plugin-actions"):
                            yield Button("创建", id="create-plugin", classes="compact primary")
                            yield Button("开启开发", id="develop-plugin", classes="compact")
                            yield Button("检查", id="check-plugin", classes="compact")
                            yield Button("结束开发", id="end-development", classes="compact")
                    with Vertical(id="result-pane"):
                        yield Static("就绪", id="operation-state", markup=False)
                        yield Static("操作结果和日志会显示在这里。", id="result-empty", markup=False)
                        yield RichLog(id="result-log", min_width=1, wrap=True, markup=False, max_lines=1000)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#plugin-section").border_title = "插件 · F6"
        self.query_one("#plugin-editor").border_title = "工作区 · F7"
        self.query_one("#result-pane").border_title = "活动输出 · F8"
        self.query_one("#plugin-table", DataTable).add_columns("插件", "状态")
        self._resize_layout()
        self._update_project()
        self._update_template_description()
        self.set_interval(2.0, self._poll_status)
        self._poll_status()

    def _update_project(self) -> None:
        project = self.backend.project
        selected = project is not None
        workspace = self.query_one("#workspace")
        workspace.set_class(selected, "has-project")
        workspace.set_class(not selected, "no-project")
        path = self.query_one("#instance-path", Static)
        path.update(f"实例  {project.root}" if selected else "")
        path.tooltip = str(project.root) if selected else None
        self.query_one("#project-path", Input).display = not selected
        self.query_one("#project-actions").display = not selected
        if selected:
            self.query_one("#project-status").display = False
        self._set_controls()

    def _show_setup_status(self, message: str, *, error: bool = False) -> None:
        status = self.query_one("#project-status", Static)
        status.update(message)
        status.display = bool(message)
        status.set_class(error, "error")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "project-path":
            self._show_setup_status("")
        elif event.input.id == "plugin-name":
            self._update_plugin_context()

    def on_resize(self, event: events.Resize) -> None:
        if self.query("#workspace"):
            self._resize_layout(event.size.width, event.size.height)

    def _resize_layout(self, width: int | None = None, height: int | None = None) -> None:
        workspace = self.query_one("#workspace")
        columns = width if width is not None else self.size.width
        rows = height if height is not None else self.size.height
        workspace.set_class(columns < 96, "narrow")
        workspace.set_class(columns < 48, "tiny")
        self.query_one("#topbar").set_class(columns < 48, "tiny")
        workspace.set_class(rows < 30, "short")

    def _set_controls(self) -> None:
        selected = self.backend.project is not None
        for widget in self.query(Button):
            needs_project = widget.id not in {"create-project", "adopt-project"}
            writes_instance = widget.id not in {"refresh", "show-logs"}
            widget.disabled = self._busy or (needs_project and not selected) or (writes_instance and self._stopping)
        for widget in self.query(Input):
            widget.disabled = self._busy
        self.query_one("#template", Select).disabled = self._busy or not selected

    def _update_template_description(self) -> None:
        chosen = self.query_one("#template", Select).value
        item = next((item for item in self._templates if item["id"] == chosen), None)
        self.query_one("#template-description", Static).update(item["description"] if item else "")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "template":
            self._update_template_description()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.data_table.get_row(event.row_key)
        self.query_one("#plugin-name", Input).value = str(row[0])

    def _update_plugin_context(self) -> None:
        name = self.query_one("#plugin-name", Input).value.strip()
        row = next((row for row in self._plugin_rows if row[0] == name), None)
        context = f"{name} · {row[1]}\n{row[2]}" if row else "输入名称创建新插件，或从左侧选择。"
        self.query_one("#plugin-context", Static).update(context)

    def _show_status(self, result: dict[str, Any]) -> None:
        running = bool(result.get("running", result.get("instance_id")))
        self._stopping = bool(result.get("stopping", False))
        state = "正在停止" if self._stopping else ("正在运行" if running else "尚未运行")
        if result.get("version"):
            state += f" · NeoBot {result['version']}"
        self.query_one("#instance-state", Static).update(state)
        self.query_one("#instance-state").tooltip = state
        self.query_one("#instance-state").set_class(running and not self._stopping, "running")
        self.query_one("#instance-state").set_class(self._stopping, "stopping")
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
        self.query_one("#plugin-empty").display = not values
        if values != self._plugin_rows:
            table = self.query_one("#plugin-table", DataTable)
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
                table.add_row(name, Text(badge, style=getattr(self.current_theme, color)))
                if reload_failed and old_rows.get(name) != (name, state, development):
                    self._write_result(f"{name} · 热重载失败", development)
            self._plugin_rows = values
        developing = sum(row[2].startswith("开发中") for row in values)
        self.query_one("#plugin-summary", Static).update(f"{len(values)} 个插件 · {developing} 开发中")
        self._update_plugin_context()

    def _write_result(self, label: str, result: Any) -> None:
        self.query_one("#workspace").add_class("has-output")
        self.query_one("#result-empty").display = False
        if self.backend.project is None:
            self.call_after_refresh(self._append_result, label, result)
        else:
            self._append_result(label, result)

    def _append_result(self, label: str, result: Any) -> None:
        log = self.query_one("#result-log", RichLog)
        log.write(label)
        log.write(format_result(result))
        log.write("")

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
            if not self.query("#instance-state"):
                return
            self._show_status(result)
            self._poll_error = ""
        except Exception as exc:
            if not self.query("#instance-state"):
                return
            message = str(exc)
            self.query_one("#instance-state", Static).update("暂时无法读取实例状态")
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
            self.query_one("#operation-state", Static).update(self.last_error)
            return
        self._busy = True
        self.last_error = ""
        self.query_one("#operation-state", Static).update(f"{label}…")
        if self.backend.project is None:
            self._show_setup_status(f"{label}…")
        self._set_controls()
        self._perform(label, operation)

    @work(group="operation")
    async def _perform(self, label: str, operation: Callable[[], Any]) -> None:
        try:
            async with self._backend_lock:
                result = await asyncio.to_thread(operation)
            if isinstance(result, dict) and result.get("ok") is False:
                self.last_error = str(result.get("error") or result.get("errors") or "操作未通过")
                self.query_one("#operation-state", Static).update(f"{label}未通过")
            else:
                if isinstance(result, dict) and any(key in result for key in ("running", "instance_id", "stopping")):
                    self._show_status(result)
                stopping = isinstance(result, dict) and bool(result.get("stopping"))
                outcome = "机器人正在停止，完成后可继续操作" if stopping else f"{label}完成"
                self.query_one("#operation-state", Static).update(outcome)
            self._write_result(label, result)
        except Exception as exc:
            self.last_error = str(exc)
            self.query_one("#operation-state", Static).update(f"失败：{exc}")
            if self.backend.project is None:
                self._show_setup_status(str(exc), error=True)
            self._write_result(f"{label}失败", self._error_details(exc))
        finally:
            self._busy = False
            self._update_project()
            self._poll_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        name = self.query_one("#plugin-name", Input).value.strip()
        path = self.query_one("#project-path", Input).value.strip()
        template = str(self.query_one("#template", Select).value)
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
                self.query_one("#operation-state", Static).update(self.last_error)
                self._show_setup_status(self.last_error, error=True)
                self.query_one("#project-path", Input).focus()
                return
            if event.button.id in {"create-plugin", "develop-plugin", "check-plugin", "end-development"} and not name:
                self.last_error = "请先填写或选择插件名称"
                self.query_one("#operation-state", Static).update(self.last_error)
                self.query_one("#plugin-name", Input).focus()
                return
            self._submit(label, action)

    def action_refresh_instance(self) -> None:
        self._poll_status()

    def action_focus_plugins(self) -> None:
        if self.backend.project is not None:
            self._focus_panel("#plugin-table", "#plugin-section")

    def action_focus_editor(self) -> None:
        if self.backend.project is not None:
            self._focus_panel("#plugin-name", "#plugin-editor")

    def action_focus_output(self) -> None:
        if self.backend.project is not None or self.query_one("#workspace").has_class("has-output"):
            self._focus_panel("#result-log", "#result-pane")

    def _focus_panel(self, control: str, panel: str) -> None:
        self.query_one(control).focus(scroll_visible=False)
        self.call_after_refresh(self.query_one(panel).scroll_visible, animate=False, immediate=True)

    def action_close_workbench(self) -> None:
        if self._busy:
            self.notify("当前操作尚未完成，请稍候再关闭控制台")
            return
        self.exit()


def run_tui(backend: WorkbenchBackend) -> None:
    WorkbenchApp(backend).run()
