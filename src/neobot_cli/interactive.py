"""Small numbered menus and prompts, using Rich only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TextIO

from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text

from .errors import CliError
from .terminal_output import Outcome, TerminalOperation, literal_text


class LiteralPrompt(Prompt):
    def render_default(self, default: Any) -> Text:
        # Sanitize only the display; an empty answer must keep the actual path.
        return literal_text(f"({default})", style="prompt.default")


class PromptStream:
    """Rich's explicit input stream treats an empty read as an empty answer."""

    def __init__(self, stream: TextIO):
        self.stream = stream

    def readline(self) -> str:
        value = self.stream.readline()
        if value == "":
            raise EOFError
        return value


class Backend(Protocol):
    project: Any

    def templates(self) -> list[dict[str, Any]]: ...
    def create_project(self, path: str | Path) -> dict: ...
    def adopt_project(self, path: str | Path) -> dict: ...
    def status(self) -> dict: ...
    def start(self) -> dict: ...
    def stop(self) -> dict: ...
    def sync(self) -> dict: ...
    def create_plugin(self, name: str, template: str) -> dict: ...
    def develop(self, name: str, enabled: bool = True) -> dict: ...
    def verify(self, name: str) -> dict: ...
    def logs(self, lines: int = 100) -> str: ...


class Menu:
    def __init__(self, backend: Backend, *, console: Console | None = None, stream: TextIO | None = None):
        self.backend = backend
        self.console = console or Console()
        self.stream = PromptStream(stream) if stream is not None else None
        self._stopping = False

    def choose(self, title: str, items: list[tuple[str, str]], *, back: str = "返回") -> str:
        self.console.print(literal_text(f"\n{title}", style="bold"))
        for number, (_, label) in enumerate(items, 1):
            self.console.print(Text.assemble((f"  {number}  ", "cyan"), literal_text(label)))
        self.console.print(literal_text(f"  0  {back}", style="dim"))
        value = LiteralPrompt.ask(
            "请选择编号",
            choices=[str(i) for i in range(len(items) + 1)],
            show_choices=False,
            console=self.console,
            stream=self.stream,
        )
        return items[int(value) - 1][0] if value != "0" else ""

    def ask(self, label: str, *, default: str = "") -> str | None:
        self.console.print("输入 :q 返回；Ctrl+C 返回主菜单。", style="dim")
        while True:
            value = LiteralPrompt.ask(
                literal_text(label), default=default or ..., console=self.console, stream=self.stream
            ).strip()
            if value == ":q":
                return None
            if value or default:
                return value or default
            self.console.print("这里不能为空，请重新输入。", style="yellow")

    def perform(self, label: str, operation: Callable[[], Any]) -> Outcome:
        def guarded():
            if self._stopping and label not in {"读取状态", "读取日志", "读取本地插件"}:
                self._stopping = bool(self.backend.status().get("stopping"))
                if self._stopping:
                    raise CliError("HOST_STOPPING", "机器人正在停止，请等待清理完成后重试。")
            return operation()

        result = TerminalOperation(self.console).run(label, guarded)
        if (
            result.ok
            and isinstance(result.value, dict)
            and any(key in result.value for key in ("running", "instance_id", "stopping"))
        ):
            self._stopping = bool(result.value.get("stopping"))
        return result

    def run(self) -> None:
        self.console.print("\nNeoBot", style="bold cyan")
        self.console.print("选一个操作，按提示继续。Ctrl+C 可退出菜单。", style="dim")
        while True:
            selected = self.backend.project is not None
            context = f"当前实例：{self.backend.project.root}" if selected else "尚未接入实例"
            self.console.print(literal_text(f"\n{context}", style="dim"))
            items = (
                [("start", "启动机器人"), ("plugin-new", "创建插件"), ("plugin-dev", "开发插件"), ("more", "更多操作")]
                if selected
                else [("new", "创建工程"), ("adopt", "接入已有工程")]
            )
            try:
                choice = self.choose("你想做什么？", items, back="退出")
            except (KeyboardInterrupt, EOFError):
                break
            if not choice:
                break
            try:
                self.dispatch(choice)
            except KeyboardInterrupt:
                self.console.print("\n已返回主菜单。", style="dim")
            except EOFError:
                break
        self.console.print("\n已退出菜单；已启动的机器人会继续运行。", style="dim")

    def dispatch(self, choice: str) -> None:
        if choice in {"new", "adopt"}:
            self.project(choice)
        elif choice == "plugin-new":
            self.new_plugin()
        elif choice == "plugin-dev":
            self.develop_plugin()
        elif choice == "more":
            self.more()
        else:
            label, operation = {
                "start": ("启动机器人", self.backend.start),
                "stop": ("停止机器人", self.backend.stop),
                "sync": ("同步环境", self.backend.sync),
                "logs": ("读取日志", self.backend.logs),
                "status": ("读取状态", self.backend.status),
            }[choice]
            self.perform(label, operation)

    def project(self, action: str) -> None:
        creating = action == "new"
        label = "创建工程" if creating else "接入已有工程"
        operation = self.backend.create_project if creating else self.backend.adopt_project
        default = str(Path.home() / "NeoBot") if creating else str(Path.cwd())
        self.console.print("新目录或已有空目录可创建；已有工程请选择接入。", style="dim")
        while (path := self.ask("实例目录", default=default)) is not None:
            before = self.backend.project
            result = self.perform(label, lambda: operation(path))
            if result.ok:
                return
            if self.backend.project is not before:
                # Creation may have succeeded while environment setup failed.
                self.console.print("工程文件已保留。可从“更多操作 → 同步环境”重试。", style="yellow")
                return
            default = path

    def new_plugin(self) -> None:
        name = self.ask("插件名称（小写字母、数字、下划线）")
        if name is None:
            return
        templates = self.backend.templates()
        template = self.choose(
            "选择插件模板", [(item["id"], f"{item['title']} — {item['description']}") for item in templates]
        )
        if template:
            self.perform("创建插件", lambda: self.backend.create_plugin(name, template))

    def develop_plugin(self) -> None:
        result = self.perform("读取本地插件", self.backend.status)
        if not result.ok:
            return
        plugins = result.value.get("plugins", [])
        if not plugins:
            self.console.print("还没有本地插件，先从主菜单创建一个。", style="dim")
            return
        items = []
        for plugin in plugins:
            name = str(plugin["name"])
            state = "开发中" if plugin.get("development") else "本地插件"
            reload = plugin.get("reload") or {}
            if reload.get("status") == "failed":
                state = "热重载失败"
            items.append((name, f"{name} · {state}"))
        name = self.choose("选择插件", items)
        if not name:
            return
        plugin = next(plugin for plugin in plugins if str(plugin["name"]) == name)
        if error := (plugin.get("reload") or {}).get("error"):
            self.console.print(literal_text(str(error), style="yellow"))
        action = self.choose(
            f"{name}：需要什么操作？",
            [("on", "开启开发（保存代码自动重载）"), ("off", "结束开发"), ("check", "检查插件")],
        )
        if action == "check":
            self.perform("检查插件", lambda: self.backend.verify(name))
        elif action:
            self.perform(
                "开启开发" if action == "on" else "结束开发", lambda: self.backend.develop(name, action == "on")
            )

    def more(self) -> None:
        choice = self.choose(
            "更多操作",
            [
                ("stop", "停止机器人"),
                ("sync", "同步环境"),
                ("status", "查看状态"),
                ("logs", "查看日志"),
                ("adopt", "接入已有工程"),
                ("new", "创建工程"),
            ],
        )
        if choice:
            self.dispatch(choice)


def run_menu(backend: Backend) -> None:
    Menu(backend).run()
