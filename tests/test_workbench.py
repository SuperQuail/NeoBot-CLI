import json
from types import SimpleNamespace

import pytest

from neobot_cli.errors import CliError
from neobot_cli.project import create_project
from neobot_cli.workbench import Workbench


@pytest.mark.parametrize(
    "requirement",
    ["requests >= 2", 'requests>=2; python_version >= "3.13"', "example @ https://example.org/example.whl"],
)
def test_sync_accepts_standard_pep508_requirements(tmp_path, monkeypatch, requirement):
    project = create_project(tmp_path / "bot", sync=False)
    plugin = project.plugin_dir / "sample"
    plugin.mkdir()
    (plugin / "plugin.toml").write_text('name="sample"\npython_dependencies=[' + json.dumps(requirement) + "]\n")
    commands = []
    monkeypatch.setattr(
        "neobot_cli.project.run_uv", lambda p, args: commands.append(args) or SimpleNamespace(returncode=0, stderr="")
    )
    backend = Workbench(project.root)
    result = backend.sync()
    assert result["synced"] is True
    assert commands[0][:3] == ["add", "--project", str(project.root)]
    assert commands[0][3] == result["requirements"][0]


def test_sync_cannot_modify_a_running_instance_environment(tmp_path, monkeypatch):
    project = create_project(tmp_path / "bot", sync=False)
    backend = Workbench(project.root)
    monkeypatch.setattr(backend, "status", lambda: {"running": True})
    with pytest.raises(CliError) as error:
        backend.sync()
    assert error.value.code == "HOST_RUNNING"


@pytest.mark.parametrize("running", [False, True])
def test_status_merges_local_plugins_with_host_and_refreshes_after_creation(tmp_path, monkeypatch, running):
    workspace = create_project(tmp_path / "bot", sync=False)
    backend = Workbench(workspace.root)
    online = {"running": True, "plugins": [{"name": "existing", "state": "running", "development": True}]}

    def status(client):
        if running:
            return online
        raise CliError("HOST_OFFLINE", "not started")

    monkeypatch.setattr("neobot_cli.workbench.ControlClient.status", status)
    monkeypatch.setattr("neobot_cli.sdk.sdk_command", lambda *args: {"ok": True})
    backend.create_plugin("existing", "command")
    backend.create_plugin("new_plugin", "event")
    result = backend.status()
    assert [item["name"] for item in result["plugins"]] == ["existing", "new_plugin"]
    assert result["plugins"][0]["state"] == ("running" if running else "unloaded")
    assert result["plugins"][0]["development"] is running
    assert result["plugins"][1]["state"] == "unloaded"
    assert online["plugins"] == [{"name": "existing", "state": "running", "development": True}]
    import shutil

    shutil.rmtree(workspace.plugin_dir / "new_plugin")
    assert [item["name"] for item in backend.status()["plugins"]] == ["existing"]


def test_generated_plugin_remains_visible_when_sdk_check_fails(tmp_path, monkeypatch):
    workspace = create_project(tmp_path / "bot", sync=False)
    backend = Workbench(workspace.root)

    def fail(*args):
        raise CliError("SDK_CHECK_FAILED", "插件检查失败")

    monkeypatch.setattr("neobot_cli.sdk.sdk_command", fail)
    with pytest.raises(CliError):
        backend.create_plugin("hello", "command")
    assert backend.status()["plugins"] == [{"name": "hello", "state": "unloaded", "development": False}]
