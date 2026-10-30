"""未来插件仓库接入点；当前版本不实现下载或安装。"""

from __future__ import annotations

from typing import Protocol

from .errors import CliError


class PluginSource(Protocol):
    def search(self, query: str) -> list[dict]: ...

    def resolve(self, reference: str) -> dict: ...


class UnavailablePluginSource:
    def search(self, query: str) -> list[dict]:
        raise CliError("NOT_IMPLEMENTED", "插件仓库尚未开放。")

    def resolve(self, reference: str) -> dict:
        raise CliError("NOT_IMPLEMENTED", "插件安装尚未开放。")
