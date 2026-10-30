"""The terminal workbench for one NeoBot instance."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Select, Static

from .errors import CliError


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
    ]
    CSS = """
    Screen { background: $background; }
    #workspace { padding: 0 1; }
    .section { height: auto; padding: 1; margin-bottom: 1; border: round $primary; }
    .heading { text-style: bold; margin-bottom: 1; }
    #instance-path, #instance-state, #template-description { height: auto; margin-bottom: 1; }
    #project-path, #plugin-name, #template { margin-bottom: 1; }
    .actions { grid-size: 2; grid-gutter: 0 1; height: auto; grid-rows: 3; }
    .actions Button { width: 1fr; min-width: 10; }
    #plugin-table { height: 6; margin-top: 1; }
    #operation-state { height: auto; padding: 0 1; }
    #result-log { height: 12; min-height: 6; border: round $secondary; margin-bottom: 1; }
    """

    def __init__(self, backend: WorkbenchBackend) -> None:
        super().__init__()
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
        yield Header()
        with VerticalScroll(id="workspace"):
            with Vertical(classes="section", id="project-section"):
                yield Label("我的 NeoBot", classes="heading")
                yield Static("还没有选择实例目录", id="instance-path", markup=False)
                yield Input(placeholder="例如 ~/NeoBot，创建时请选择新目录", id="project-path")
                with Grid(classes="actions", id="project-actions"):
                    yield Button("创建工程", id="create-project", variant="primary")
                    yield Button("接入已有工程", id="adopt-project")
            with Vertical(classes="section", id="instance-section"):
                yield Static("选择工程后即可查看运行状态", id="instance-state", markup=False)
                with Grid(classes="actions"):
                    yield Button("启动机器人", id="start", variant="primary")
                    yield Button("停止机器人", id="stop")
                    yield Button("刷新状态", id="refresh")
                    yield Button("查看日志", id="show-logs")
                    yield Button("同步环境", id="sync-environment")
            with Vertical(classes="section", id="plugin-section"):
                yield Label("在当前实例开发插件", classes="heading")
                yield Select(
                    [(item["title"], item["id"]) for item in self._templates],
                    allow_blank=False,
                    id="template",
                )
                yield Static(id="template-description", markup=False)
                yield Input(placeholder="插件名称，例如 hello", id="plugin-name")
                with Grid(classes="actions"):
                    yield Button("创建插件", id="create-plugin", variant="primary")
                    yield Button("开启开发", id="develop-plugin")
                    yield Button("检查插件", id="check-plugin")
                    yield Button("结束开发", id="end-development")
                yield DataTable(id="plugin-table", cursor_type="row", zebra_stripes=True)
            yield Static("就绪", id="operation-state", markup=False)
            yield RichLog(id="result-log", wrap=True, markup=False, max_lines=1000)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#plugin-table", DataTable).add_columns("插件", "状态", "开发与重载")
        self._update_project()
        self._update_template_description()
        self.set_interval(2.0, self._poll_status)
        self._poll_status()

    def _update_project(self) -> None:
        project = self.backend.project
        selected = project is not None
        self.query_one("#instance-path", Static).update(str(project.root) if selected else "还没有选择实例目录")
        self.query_one("#project-path", Input).display = not selected
        self.query_one("#project-actions").display = not selected
        self._set_controls()

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

    def _show_status(self, result: dict[str, Any]) -> None:
        running = bool(result.get("running", result.get("instance_id")))
        self._stopping = bool(result.get("stopping", False))
        state = "正在停止" if self._stopping else ("正在运行" if running else "尚未运行")
        if result.get("version"):
            state += f" · NeoBot {result['version']}"
        self.query_one("#instance-state", Static).update(state)
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
        if values != self._plugin_rows:
            table = self.query_one("#plugin-table", DataTable)
            table.clear()
            table.add_rows(values)
            self._plugin_rows = values

    def _write_result(self, label: str, result: Any) -> None:
        log = self.query_one("#result-log", RichLog)
        log.write(label)
        if isinstance(result, str):
            log.write(result)
        else:
            log.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))

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

    def action_close_workbench(self) -> None:
        if self._busy:
            self.notify("当前操作尚未完成，请稍候再关闭控制台")
            return
        self.exit()


def run_tui(backend: WorkbenchBackend) -> None:
    WorkbenchApp(backend).run()
