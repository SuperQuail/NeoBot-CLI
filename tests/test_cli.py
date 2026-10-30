import json
import subprocess
import sys

import pytest

from neobot_cli.cli import main
from neobot_cli.errors import CliError
from neobot_cli.providers import UnavailablePluginSource


@pytest.mark.parametrize(
    "arguments",
    [
        ["--json"],
        ["plugin", "new", "--json"],
        ["--json", "missing-command"],
        ["logs", "--lines", "no", "--json"],
        ["--json", "--no-input", "tui"],
    ],
)
def test_all_early_errors_are_structured(arguments, capsys):
    assert main(arguments) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["schema_version"] == 1
    assert report["ok"] is False
    assert captured.err == ""


def test_schema_is_generated_from_parser_and_install_is_unavailable(capsys):
    assert main(["schema", "plugin.new", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["capabilities"]["plugin.install"] is False
    command = data["commands"][0]
    assert command["command"] == "plugin.new"
    template = next(item for item in command["parameters"] if item["name"] == "template")
    assert template["choices"] == ["command", "event", "page"]
    with pytest.raises(CliError) as error:
        UnavailablePluginSource().resolve("example/plugin")
    assert error.value.code == "NOT_IMPLEMENTED"


def test_no_command_without_tty_shows_help_without_importing_tui(capsys, monkeypatch):
    monkeypatch.setattr("neobot_cli.cli._interactive", lambda: False)
    assert main([]) == 0
    assert "NeoBot" in capsys.readouterr().out
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from neobot_cli.cli import main; import sys; main([]); assert 'textual' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_project_creation_from_cli_is_machine_readable(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("NEOBOT_CLI_CONFIG_DIR", str(tmp_path / "preferences"))
    assert main(["project", "new", str(tmp_path / "bot"), "--no-sync", "--json", "--no-input"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["root"] == str(tmp_path / "bot")


def test_explicit_command_does_not_launch_tui(capsys, monkeypatch):
    monkeypatch.setattr("neobot_cli.cli._interactive", lambda: True)
    assert main(["plugin", "templates", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["data"]["templates"]) == 3


def test_adopt_command_repairs_moved_instance_from_inside_it(tmp_path, capsys, monkeypatch):
    from neobot_cli.project import create_project

    monkeypatch.setenv("NEOBOT_CLI_CONFIG_DIR", str(tmp_path / "preferences"))
    original = create_project(tmp_path / "old", sync=False)
    moved = tmp_path / "new"
    original.root.rename(moved)
    monkeypatch.chdir(moved)
    assert main(["project", "adopt", ".", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["root"] == str(moved)


@pytest.mark.parametrize(("arguments", "expected"), [(["plugin", "templates", "--json"], 0), (["--json"], 2)])
def test_json_survives_non_utf8_output_encoding(arguments, expected):
    import os

    result = subprocess.run(
        [sys.executable, "-m", "neobot_cli", *arguments],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert result.returncode == expected, result.stderr
    result.stdout.encode("ascii")
    report = json.loads(result.stdout)
    assert report["ok"] is (expected == 0)
    if expected == 0:
        assert report["data"]["templates"][0]["title"] == "聊天命令"
    else:
        assert report["error"]["code"] == "COMMAND_REQUIRED"
