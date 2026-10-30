"""通过 uv 启动实例；进程停止只走已认证的宿主接口。"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager

from .control import ControlClient
from .errors import CliError
from .project import Workspace, uv_command
from .progress import stage


@contextmanager
def _launch_lock(project: Workspace):
    path = project.root / ".neobot/launch.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt

                if not path.stat().st_size:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CliError("START_IN_PROGRESS", "当前实例正在启动，请稍后刷新状态。") from exc
        yield
    finally:
        stream.close()


def start(project: Workspace, *, timeout: float = 45) -> dict:
    stage("检查实例状态", "已运行的实例会直接接入。")
    client = ControlClient(project, timeout=2)
    with _launch_lock(project):
        try:
            state = client.status()
            if state.get("stopping"):
                raise CliError("HOST_STOPPING", "实例正在停止，请等清理完成后再启动。")
            return state
        except CliError as error:
            if error.code not in {"HOST_OFFLINE", "HOST_UNAVAILABLE"}:
                raise
        environment = os.environ.copy()
        environment.update(
            NEOBOT_DATA_DIR=str(project.data_dir), NEOBOT_ENV_FILE=str(project.env_file), PYTHONUNBUFFERED="1"
        )
        environment.pop("VIRTUAL_ENV", None)
        logfile = project.root / ".neobot/host.log"
        logfile.parent.mkdir(parents=True, exist_ok=True)
        options = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        with logfile.open("ab") as output:
            process = subprocess.Popen(
                [uv_command(), "run", "--locked", "--project", str(project.root), "neobot"],
                cwd=project.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                **options,
            )
        deadline = time.monotonic() + timeout
        stage("等待机器人就绪", "正在启动实例，准备控制服务；启动失败可在概览中读取机器人日志。")
        while time.monotonic() < deadline:
            try:
                return client.status()
            except CliError as error:
                if error.code not in {"HOST_OFFLINE", "HOST_UNAVAILABLE"}:
                    raise
            if process.poll() is not None:
                raise CliError("START_FAILED", "实例启动失败。", hint=f"查看 {logfile}")
            time.sleep(0.2)
        # The host may still finish starting; never launch another copy on retry.
        raise CliError("START_TIMEOUT", "实例仍未提供控制服务，请先查看日志再刷新状态。", hint=str(logfile))


def stop(project: Workspace) -> dict:
    client = ControlClient(project)
    # Validate instance identity before sending a mutating request.
    client.status()
    return client.request("POST", "stop", {})


def logs(project: Workspace, lines: int = 100) -> str:
    if not 1 <= lines <= 10000:
        raise CliError("INVALID_ARGUMENT", "日志行数必须在 1 到 10000 之间。")
    path = project.root / ".neobot/host.log"
    if not path.exists():
        return "暂无由工作台启动的实例日志。"
    # Bound reads even for long-running deployments.
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 1024 * 1024))
        return "\n".join(stream.read().decode("utf-8", errors="replace").splitlines()[-lines:])
