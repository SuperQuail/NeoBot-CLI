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
