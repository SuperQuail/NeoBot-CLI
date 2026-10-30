"""显式命令直达；只有交互终端无参数才进入 Textual。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .errors import CliError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError("INVALID_ARGUMENT", message)


def _common(parser: argparse.ArgumentParser):
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="输出结构化结果")
    parser.add_argument("--no-input", action="store_true", default=argparse.SUPPRESS, help="禁止交互提问")
    parser.add_argument("--project", type=Path, default=argparse.SUPPRESS, help="实例目录，也可在其子目录内运行")
    parser.add_argument(
        "--source", type=Path, default=argparse.SUPPRESS, help="创建工程时使用本地 NeoBot 源码（开发版）"
    )


def parser() -> Parser:
    result = Parser(prog="neo", description="NeoBot 单实例开发工作台；无参数在交互终端打开 Textual。")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _common(result)
    commands = result.add_subparsers(dest="command")
    for name, help_text in {
        "tui": "打开交互工作台",
        "start": "启动或连接当前实例",
        "stop": "停止当前实例",
        "status": "查询实例与开发监听状态",
        "sync": "同步实例和本地插件的 Python 依赖",
    }.items():
        _common(commands.add_parser(name, help=help_text))
    logs = commands.add_parser("logs", help="查看由工作台启动的实例日志")
    _common(logs)
    logs.add_argument("--lines", type=int, default=100)
    project = commands.add_parser("project", help="创建或接管实例")
    _common(project)
    projects = project.add_subparsers(dest="project_command", required=True)
    for name in ("new", "adopt"):
        command = projects.add_parser(name, help="创建实例" if name == "new" else "接管已有实例")
        _common(command)
        command.add_argument("path", type=Path)
        if name == "new":
            command.add_argument("--no-sync", action="store_true", help="只创建文件；稍后显式执行 neo sync")
    plugin = commands.add_parser("plugin", help="当前实例的本地插件开发")
    _common(plugin)
    plugins = plugin.add_subparsers(dest="plugin_command", required=True)
    choices = plugins.add_parser("templates", help="查看模板")
    _common(choices)
    new = plugins.add_parser("new", help="从模板创建本地插件")
    _common(new)
    new.add_argument("name")
    new.add_argument("--template", default="command", choices=("command", "event", "page"))
    for name in ("dev", "reload", "verify", "export"):
        command = plugins.add_parser(name)
        _common(command)
        command.add_argument("name")
        if name == "dev":
            command.add_argument("--off", action="store_true", help="结束该插件的开发监听，不停止插件")
    schema = commands.add_parser("schema", help="导出命令定义，供脚本和开发助手发现能力")
    _common(schema)
    schema.add_argument("name", nargs="?")
    return result


def command_schema(root: argparse.ArgumentParser) -> list[dict]:
    entries = []

    def visit(current, path):
        parameters = []
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    if name != "install":
                        visit(child, [*path, name])
            elif action.dest not in {"help", "version"}:
                parameters.append(
                    {
                        "name": action.dest,
                        "flags": action.option_strings,
                        "required": action.required,
                        "choices": list(action.choices) if action.choices else None,
                    }
                )
        if path:
            entries.append({"command": ".".join(path), "parameters": parameters})

    visit(root, [])
    return entries


def _dispatch(arguments, root):
    if arguments.command == "schema":
        entries = command_schema(root)
        if arguments.name:
            entries = [entry for entry in entries if entry["command"] == arguments.name]
            if not entries:
                raise CliError("COMMAND_NOT_FOUND", "找不到这个命令定义。")
        return {"commands": entries, "capabilities": {"plugin.install": False}}
    from .workbench import Workbench

    backend = Workbench(
        getattr(arguments, "project", None),
        source=getattr(arguments, "source", None),
        load_project=arguments.command != "project",
    )
    if arguments.command == "tui":
        if getattr(arguments, "no_input", False) or getattr(arguments, "json", False) or not _interactive():
            raise CliError("TTY_REQUIRED", "Textual 需要可交互终端；自动化请使用明确子命令。")
        from .tui import run_tui

        run_tui(backend)
        return None
    if arguments.command == "project":
        if arguments.project_command == "new":
            return backend.create_project(arguments.path, sync=not arguments.no_sync)
        return backend.adopt_project(arguments.path)
    if arguments.command == "logs":
        return {"logs": backend.logs(arguments.lines)}
    if arguments.command == "plugin":
        command = arguments.plugin_command
        if command == "templates":
            return {"templates": backend.templates()}
        if command == "new":
            return backend.create_plugin(arguments.name, arguments.template)
        if command == "dev":
            return backend.develop(arguments.name, enabled=not arguments.off)
        return getattr(backend, command)(arguments.name)
    return getattr(backend, arguments.command)()


def _interactive():
    return sys.stdin.isatty() and sys.stdout.isatty() and os.getenv("TERM") != "dumb"


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    machine = "--json" in values
    root = parser()
    try:
        arguments = root.parse_args(values)
        if arguments.command is None:
            if _interactive() and not machine and not getattr(arguments, "no_input", False):
                arguments.command = "tui"
            elif machine or getattr(arguments, "no_input", False):
                raise CliError("COMMAND_REQUIRED", "请提供要执行的命令。")
            else:
                root.print_help()
                return 0
        data = _dispatch(arguments, root)
        if data is not None:
            if machine:
                print(json.dumps({"schema_version": 1, "ok": True, "data": data}, ensure_ascii=True))
            else:
                print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except CliError as error:
        if machine:
            print(json.dumps({"schema_version": 1, "ok": False, "error": error.to_dict()}, ensure_ascii=True))
        else:
            print(str(error), file=sys.stderr)
            if error.hint:
                print(error.hint, file=sys.stderr)
            if error.details:
                print(json.dumps(error.details, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        public = CliError("OPERATION_FAILED", str(error))
        if machine:
            print(json.dumps({"schema_version": 1, "ok": False, "error": public.to_dict()}, ensure_ascii=True))
        else:
            print(str(public), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
