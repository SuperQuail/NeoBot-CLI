"""仅连接当前实例的本机控制服务；凭据不出现在公开报告中。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .errors import CliError
from .project import Workspace


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ControlClient:
    def __init__(self, project: Workspace, *, timeout: float = 15):
        self.project = project
        self.timeout = timeout

    def _connection(self) -> dict:
        path = self.project.data_dir / ".neobot/control.json"
        try:
            if path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError("invalid discovery file")
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("invalid discovery document")
            parsed = urllib.parse.urlsplit(document["url"])
            if (
                document.get("schema_version") != 1
                or parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or not parsed.port
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username
                or Path(document["data_dir"]).resolve() != self.project.data_dir
                or not isinstance(document.get("token"), str)
                or not document["token"]
                or not isinstance(document.get("instance_id"), str)
                or not document["instance_id"]
            ):
                raise ValueError("invalid connection metadata")
            return document
        except FileNotFoundError as exc:
            raise CliError(
                "HOST_OFFLINE", "当前实例未提供控制服务。", hint="先启动实例；旧版本需要升级后重启一次。"
            ) from exc
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CliError("CONTROL_INVALID", "实例控制记录无效，请检查实例目录。") from exc

    def request(self, method: str, endpoint: str, body: dict | None = None) -> dict:
        connection = self._connection()
        request = urllib.request.Request(
            connection["url"].rstrip("/") + "/v1/" + endpoint,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": "Bearer " + connection["token"], "Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CliError("HOST_UNAVAILABLE", "无法连接当前实例。请检查状态或运行日志。") from exc
        try:
            with response:
                if 300 <= response.status < 400:
                    raise ValueError("redirects are not supported")
                payload = response.read(1024 * 1024 + 1)
                if len(payload) > 1024 * 1024:
                    raise ValueError("response too large")
                result = json.loads(payload)
            if result.get("schema_version") != 1 or not isinstance(result.get("ok"), bool):
                raise ValueError("unsupported protocol")
        except (ValueError, OSError, AttributeError) as exc:
            raise CliError("PROTOCOL_INCOMPATIBLE", "宿主控制协议不兼容，请检查 CLI 与 NeoBot 版本。") from exc
        if not result["ok"]:
            error = result.get("error", {})
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("code"), str)
                or not isinstance(error.get("message"), str)
            ):
                raise CliError("PROTOCOL_INCOMPATIBLE", "宿主返回了无效错误结果。")
            raise CliError(error.get("code", "HOST_ERROR"), error.get("message", "宿主操作失败。"))
        data = result.get("data", {})
        if not isinstance(data, dict):
            raise CliError("PROTOCOL_INCOMPATIBLE", "宿主返回了无效结果。")
        if endpoint == "status":
            if any(not isinstance(data.get(key), str) for key in ("instance_id", "data_dir", "plugin_dir")):
                raise CliError("PROTOCOL_INCOMPATIBLE", "宿主实例信息缺少有效路径。")
            if (
                data["instance_id"] != connection["instance_id"]
                or Path(data["data_dir"]).resolve() != self.project.data_dir
                or Path(data["plugin_dir"]).resolve() != self.project.plugin_dir
            ):
                raise CliError("INSTANCE_MISMATCH", "控制服务与当前实例目录不一致。")
        return data

    def status(self) -> dict:
        return {"running": True, **self.request("GET", "status")}
