from pathlib import Path
import tomllib

import pytest

from neobot_cli.errors import CliError
from neobot_cli.project import adopt_project, create_project, discover_project
from neobot_cli.workbench import Workbench


def test_create_and_find_from_plugin_directory(tmp_path):
    project = create_project(tmp_path / "我的机器人", sync=False)
    nested = project.plugin_dir / "hello/tests"
    nested.mkdir(parents=True)
    assert discover_project(nested) == project
    assert project.plugin_dir == project.data_dir / "plugins"
    metadata = tomllib.loads((project.root / "pyproject.toml").read_text())
    assert metadata["project"]["dependencies"] == ["neobot-app>=2.0.2a14"]
    assert "pytest>=9,<10" in metadata["dependency-groups"]["dev"]
    sources = metadata["tool"]["uv"]["sources"]
    assert len(sources) == 7
    assert len({item["rev"] for item in sources.values()}) == 1
    assert not (project.plugin_dir / ".venv").exists()


def test_existing_directory_is_not_overwritten(tmp_path):
    path = tmp_path / "bot"
    path.mkdir()
    (path / "keep").write_text("original")
    with pytest.raises(CliError, match="目标目录已存在"):
        create_project(path, sync=False)
    assert (path / "keep").read_text() == "original"


def test_adopt_preserves_config_and_uses_configured_plugin_dir(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="bot"\nversion="0.1.0"\n')
    (tmp_path / "data").mkdir()
    config = tmp_path / "data/config.toml"
    original = '[plugins]\ndir = "my-plugins"\n'
    config.write_text(original)
    workspace = adopt_project(tmp_path)
    assert workspace.plugin_dir == tmp_path / "data/my-plugins"
    assert config.read_text() == original


def test_local_source_uses_all_packages_without_copying_source(tmp_path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "app/pyproject.toml").write_text('[project]\nname="neobot-app"\n')
    package = source / "packages/contracts"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text('[project]\nname="neobot-contracts"\n')
    workspace = create_project(tmp_path / "bot", source=source, sync=False)
    metadata = tomllib.loads((workspace.root / "pyproject.toml").read_text())
    assert metadata["tool"]["uv"]["sources"]["neobot-contracts"]["path"] == str(package)
    assert not (workspace.root / "app").exists()


def test_default_instance_is_remembered_but_explicit_directory_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("NEOBOT_CLI_CONFIG_DIR", str(tmp_path / "prefs"))
    monkeypatch.chdir(tmp_path)
    first = Workbench()
    first.create_project(tmp_path / "bot", sync=False)
    assert Workbench().project == first.project
    second = create_project(tmp_path / "other", sync=False)
    assert Workbench(second.root).project == second
    assert Workbench(tmp_path / "unknown").project is None


def test_moved_instance_requires_readoption(tmp_path):
    workspace = create_project(tmp_path / "old", sync=False)
    moved = tmp_path / "moved"
    workspace.root.rename(moved)
    with pytest.raises(CliError) as error:
        discover_project(moved)
    assert error.value.code == "PROJECT_INVALID"
    assert adopt_project(moved).root == moved
    assert discover_project(moved).data_dir == moved / "data"


def test_readoption_refreshes_changed_configuration(tmp_path):
    project = create_project(tmp_path / "bot", sync=False)
    (project.data_dir / "config.toml").write_text('[plugins]\ndir="custom"\n')
    adopted = adopt_project(project.root)
    assert adopted.plugin_dir == project.data_dir / "custom"
    assert discover_project(project.root).plugin_dir == adopted.plugin_dir


def test_invalid_source_leaves_no_project(tmp_path):
    with pytest.raises(CliError):
        create_project(tmp_path / "bot", source=Path("/nonexistent-neobot-source"), sync=False)
    assert not (tmp_path / "bot").exists()
