"""命令行、Textual 共用的单实例工作流。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

from . import process, project, sdk, templates
from .control import ControlClient
from .errors import CliError


class Workbench:
    def __init__(
        self, project_path: str | Path | None = None, *, source: str | Path | None = None, load_project: bool = True
    ):
        self.project = project.discover_project(project_path) if load_project else None
        if self.project is None and project_path is None and load_project:
            self.project = project.default_project()
        self.source = Path(source).resolve() if source else None

    def _project(self) -> project.Workspace:
        if self.project is None:
            raise CliError("PROJECT_REQUIRED", "先创建或接管一个 NeoBot 实例。")
        return self.project

    def _plugin(self, name: str) -> Path:
        workspace = self._project()
        if (
            not name
            or name.startswith(".")
            or not all(character.isascii() and (character.isalnum() or character == "_") for character in name)
        ):
            raise CliError("PLUGIN_NAME_INVALID", "插件名称只能使用英文字母、数字和下划线。")
        path = workspace.plugin_dir / name
        if path.is_symlink() or path.resolve().parent != workspace.plugin_dir:
            raise CliError("PLUGIN_PATH_INVALID", "插件必须直接位于当前实例的插件目录中。")
        if not path.is_dir():
            raise CliError("PLUGIN_NOT_FOUND", f"找不到本地插件 {name}。")
        return path

    def templates(self) -> list[dict]:
        return templates.list_templates()

    def create_project(self, path: str | Path, *, sync: bool = True) -> dict:
        try:
            self.project = project.create_project(path, source=self.source, sync=sync)
        except CliError as error:
            # A sync failure retains a usable project for a subsequent retry.
            if error.code == "SYNC_FAILED":
                self.project = project.discover_project(path)
                if self.project is not None:
                    project.remember_project(self.project)
            raise
        project.remember_project(self.project)
        return self.project.to_dict()

    def adopt_project(self, path: str | Path) -> dict:
        self.project = project.adopt_project(path)
        project.remember_project(self.project)
        return self.project.to_dict()

    def status(self) -> dict:
        workspace = self._project()
        try:
            return ControlClient(workspace).status()
        except CliError as error:
            if error.code not in {"HOST_OFFLINE", "HOST_UNAVAILABLE"}:
                raise
            return {"running": False, **workspace.to_dict(), "plugins": [], "reason": error.code}

    def start(self) -> dict:
        return process.start(self._project())

    def stop(self) -> dict:
        return process.stop(self._project())

    def logs(self, lines: int = 100) -> str:
        return process.logs(self._project(), lines)

    def create_plugin(self, name: str, template: str = "command") -> dict:
        workspace = self._project()
        try:
            path = templates.create_plugin(workspace.plugin_dir, name, template)
        except (ValueError, OSError) as exc:
            raise CliError("TEMPLATE_FAILED", str(exc)) from exc
        exported = sdk.sdk_command(workspace, "export", path)
        checked = sdk.sdk_command(workspace, "check", path)
        return {
            "name": name,
            "path": str(path),
            "template": template,
            "reports": [exported, checked],
            "next": f"neo plugin dev {name}",
        }

    def develop(self, name: str, enabled: bool = True) -> dict:
        self._plugin(name)
        client = ControlClient(self._project())
        state = client.status()
        if "plugin.dev" not in state.get("capabilities", []):
            raise CliError("CAPABILITY_UNAVAILABLE", "当前 NeoBot 不支持同实例开发，请升级宿主。")
        return client.request("POST", "plugins/dev", {"name": name, "enabled": enabled})

    def reload(self, name: str) -> dict:
        self._plugin(name)
        client = ControlClient(self._project())
        client.status()
        return client.request("POST", "plugins/reload", {"name": name})

    def verify(self, name: str) -> dict:
        return sdk.verify(self._project(), self._plugin(name))

    def export(self, name: str) -> dict:
        return sdk.sdk_command(self._project(), "export", self._plugin(name))

    def sync(self) -> dict:
        workspace = self._project()
        if self.status()["running"]:
            raise CliError("HOST_RUNNING", "请先停止实例再同步 Python 依赖；普通代码修改仍可直接热重载。")
        # Keep runtime requirements declared in uv, so exact sync cannot erase them.
        requirements = set()
        for manifest in sorted(workspace.plugin_dir.glob("*/plugin.toml")):
            if manifest.parent.is_symlink():
                continue
            try:
                document = tomllib.loads(manifest.read_text(encoding="utf-8"))
                values = document.get("python_dependencies", [])
                if not isinstance(values, list) or any(
                    not isinstance(item, str) or item.lstrip().startswith("-") for item in values
                ):
                    raise ValueError("invalid dependency")
                requirements.update(str(Requirement(item)) for item in values)
            except (OSError, ValueError, TypeError) as exc:
                raise CliError("MANIFEST_INVALID", f"插件依赖声明无效：{manifest.parent.name}") from exc
        if requirements:
            arguments = ["add", "--project", str(workspace.root), *sorted(requirements)]
        else:
            arguments = ["sync", "--project", str(workspace.root)]
        result = project.run_uv(workspace, arguments)
        if result.returncode:
            raise CliError(
                "SYNC_FAILED", "依赖同步失败，请检查版本冲突或网络。", details={"stderr": result.stderr[-4000:]}
            )
        return {"synced": True, "requirements": sorted(requirements), "restart_required": self.status()["running"]}
