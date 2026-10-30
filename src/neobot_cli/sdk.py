"""在实例锁定环境执行公开 SDK；不在控制台进程导入宿主代码。"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import CliError
from .project import Workspace, run_uv


def sdk_command(project: Workspace, command: str, path: Path, *options: str) -> dict:
    result = run_uv(
        project,
        [
            "run",
            "--locked",
            "--project",
            str(project.root),
            "python",
            "-m",
            "neobot_modloader.sdk",
            command,
            str(path),
            *options,
            "--json",
        ],
    )
    try:
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or not isinstance(report.get("ok"), bool):
            raise ValueError("invalid SDK report")
    except ValueError as exc:
        raise CliError(
            "SDK_UNAVAILABLE",
            "实例 SDK 没有返回有效报告。请检查版本与环境。",
            details={"stderr": result.stderr[-4000:]},
        ) from exc
    if result.returncode or not report["ok"]:
        raise CliError("SDK_CHECK_FAILED", f"插件 {command} 失败。", details=report)
    return report


def verify(project: Workspace, path: Path) -> dict:
    reports = [sdk_command(project, "check", path), sdk_command(project, "export", path, "--check")]
    tests = path / "tests"
    if not tests.is_dir() or not any(tests.rglob("test_*.py")):
        raise CliError("TESTS_REQUIRED", "插件需要至少一个可执行的业务测试。")
    artifact_dir = project.root / ".neobot/artifacts" / path.name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    test_report = artifact_dir / "tests.json"
    test_report.unlink(missing_ok=True)
    result = run_uv(
        project,
        [
            "run",
            "--locked",
            "--project",
            str(project.root),
            "python",
            str(Path(__file__).with_name("_pytest_runner.py")),
            str(tests),
            str(test_report),
        ],
    )
    try:
        counts = json.loads(test_report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        counts = {"ok": False}
    if result.returncode or not counts.get("ok"):
        raise CliError(
            "TEST_FAILED",
            "插件业务测试未全部通过（不接受跳过、xfail、xpass 或零用例）。",
            details={"counts": counts, "output": (result.stdout + result.stderr)[-8000:]},
        )
    first = sdk_command(project, "build", path, "--output", str(artifact_dir / "plugin.zip"))
    second = sdk_command(project, "build", path, "--output", str(artifact_dir / "plugin-repeat.zip"))
    if not first.get("sha256") or first["sha256"] != second.get("sha256"):
        raise CliError("BUILD_NOT_DETERMINISTIC", "两次构建的插件摘要不一致。")
    reports.extend([first, sdk_command(project, "smoke", artifact_dir / "plugin.zip")])
    return {
        "plugin": path.name,
        "reports": reports,
        "tests": counts,
        "artifact": first.get("path"),
        "sha256": first["sha256"],
        "unverified": ["真实聊天平台与外部服务联调"],
    }
