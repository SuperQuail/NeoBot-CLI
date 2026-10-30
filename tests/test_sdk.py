from types import SimpleNamespace
import json

import pytest

from neobot_cli.errors import CliError
from neobot_cli.project import create_project
from neobot_cli.sdk import sdk_command


def test_sdk_runs_in_locked_instance_instead_of_tool_environment(tmp_path, monkeypatch):
    workspace = create_project(tmp_path / "bot", sync=False)
    calls = []

    def run(project, args):
        calls.append((project, args))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True}), stderr="")

    monkeypatch.setattr("neobot_cli.sdk.run_uv", run)
    sdk_command(workspace, "check", workspace.plugin_dir / "hello")
    assert calls[0][0] is workspace
    assert calls[0][1][:6] == ["run", "--locked", "--project", str(workspace.root), "python", "-m"]


def test_sdk_failure_keeps_failed_report(tmp_path, monkeypatch):
    workspace = create_project(tmp_path / "bot", sync=False)
    report = {"ok": False, "errors": ["contract expired"]}
    monkeypatch.setattr(
        "neobot_cli.sdk.run_uv", lambda *args: SimpleNamespace(returncode=1, stdout=json.dumps(report), stderr="")
    )
    with pytest.raises(CliError) as error:
        sdk_command(workspace, "check", workspace.plugin_dir / "hello")
    assert error.value.details == report
