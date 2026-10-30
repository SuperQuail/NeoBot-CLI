"""Bundled plugin templates; generation never imports or executes plugin code."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path


_TEMPLATES = (
    {
        "id": "command",
        "title": "聊天命令",
        "description": "创建可配置的 /hello 命令，适合签到、查询和消息回复。",
        "requires_frontend": False,
    },
    {
        "id": "event",
        "title": "消息事件",
        "description": "收到指定私聊消息时回复，适合消息监听与自动处理。",
        "requires_frontend": False,
    },
    {
        "id": "page",
        "title": "自定义页面",
        "description": "创建配置表单、只读动作和 HTML 页面，无需 Node。",
        "requires_frontend": False,
    },
)


def list_templates() -> list[dict]:
    """Return fresh descriptors so callers cannot mutate the template registry."""
    return [dict(template) for template in _TEMPLATES]


def _validate_name(name: str) -> None:
    # A portable Python package name also avoids Windows device names and path syntax.
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or "__" in name:
        raise ValueError("插件名须以小写字母开头，只能包含小写字母、数字和单个下划线，最长 64 字符")
    if name.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ValueError("插件名不能使用 Windows 保留设备名")


def _reject_symlink_components(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("插件目录不能经过符号链接，请使用实际实例目录")


def _copy_assets(source, target: Path, name: str) -> None:
    for asset in source.iterdir():
        relative = asset.name.removesuffix(".txt")
        destination = target / relative
        if asset.is_dir():
            destination.mkdir(exist_ok=True)
            _copy_assets(asset, destination, name)
        else:
            destination.write_text(
                asset.read_text(encoding="utf-8").replace("__PLUGIN_NAME__", name), encoding="utf-8", newline="\n"
            )


def create_plugin(plugin_dir: Path, name: str, template: str = "command") -> Path:
    """Create a local source tree without overwriting any existing path.

    The caller must export and validate the contract in the instance's SDK environment
    before loading this source tree. No environment or second Bot is created here.
    """
    _validate_name(name)
    if template not in {item["id"] for item in _TEMPLATES}:
        raise ValueError(f"未知插件模板：{template}")
    root = Path(os.path.abspath(plugin_dir))
    _reject_symlink_components(root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / name
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"插件目录已存在：{destination}")
    assets = files("neobot_cli").joinpath("template_assets")
    with tempfile.TemporaryDirectory(prefix=".neobot-plugin-", dir=root) as temporary:
        staging = Path(temporary) / name
        staging.mkdir()
        _copy_assets(assets.joinpath("common"), staging, name)
        _copy_assets(assets.joinpath(template), staging, name)
        # mkdir is the no-overwrite reservation, including concurrent creators.
        destination.mkdir()
        try:
            shutil.copytree(staging, destination, dirs_exist_ok=True)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return destination
