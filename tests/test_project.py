from pathlib import Path
import tomllib

import pytest

from neobot_cli.errors import CliError
from neobot_cli import project as project_module
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
    with pytest.raises(CliError, match="目标目录已存在") as error:
        create_project(path, sync=False)
    assert error.value.code == "PROJECT_EXISTS"
    assert "非空" in str(error.value)
    assert "空目录" in error.value.hint
    assert "子目录" in error.value.hint
    assert (path / "keep").read_text() == "original"


def test_create_in_existing_empty_directory(tmp_path):
    root = tmp_path / "空目录"
    root.mkdir()
    original = root.stat()
    project = create_project(root, sync=False)
    assert root.stat().st_ino == original.st_ino
    assert project.root == root
    assert project.plugin_dir.is_dir()
    assert discover_project(root) == project


@pytest.mark.parametrize("marker", ["pyproject.toml", ".neobot/project.json"])
def test_existing_project_suggests_adoption_without_overwriting(tmp_path, marker):
    root = tmp_path / "bot"
    existing = root / marker
    existing.parent.mkdir(parents=True)
    existing.write_text("original")
    with pytest.raises(CliError, match="接入已有工程") as error:
        create_project(root, sync=False)
    assert error.value.code == "PROJECT_EXISTS"
    assert "neo project adopt" in error.value.hint
    assert existing.read_text() == "original"
    assert [path for path in root.rglob("*") if path.is_file()] == [existing]


def test_file_target_is_preserved_with_actionable_hint(tmp_path):
    root = tmp_path / "bot"
    root.write_text("original")
    with pytest.raises(CliError, match="文件") as error:
        create_project(root, sync=False)
    assert error.value.code == "PROJECT_EXISTS"
    assert "空目录" in error.value.hint
    assert root.read_text() == "original"


@pytest.mark.parametrize("kind", ["directory", "file", "dangling"])
def test_symlink_target_is_not_followed(tmp_path, kind):
    target = tmp_path / "target"
    if kind == "directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("original")
    root = tmp_path / "bot"
    root.symlink_to(target, target_is_directory=kind == "directory")
    with pytest.raises(CliError, match="符号链接") as error:
        create_project(root, sync=False)
    assert "真实的空目录" in error.value.hint
    assert root.is_symlink()
    if kind == "directory":
        assert not list(target.iterdir())
    elif kind == "file":
        assert target.read_text() == "original"
    else:
        assert not target.exists()


def test_path_under_file_is_reported_as_invalid(tmp_path):
    parent = tmp_path / "file"
    parent.write_text("original")
    with pytest.raises(CliError) as error:
        create_project(parent / "bot", sync=False)
    assert error.value.code == "PROJECT_PATH_INVALID"
    assert "路径" in error.value.hint
    assert parent.read_text() == "original"


@pytest.mark.parametrize("failure", ["file_parent", "permission_denied"])
def test_parent_creation_failure_after_missing_target_is_reported_as_invalid(tmp_path, monkeypatch, failure):
    parent = tmp_path / "parent"
    if failure == "file_parent":
        parent.write_text("original")
    else:
        parent.mkdir()
    root = parent / "bot"
    original_lstat, original_mkdir = Path.lstat, Path.mkdir
    original_errors = []

    def missing_target(path, *args, **kwargs):
        if path == root:
            # Windows reports this even when an intermediate component is a regular file.
            raise FileNotFoundError(2, "No such file or directory", str(root))
        return original_lstat(path, *args, **kwargs)

    def create_parent(path, *args, **kwargs):
        try:
            if path == parent and failure == "permission_denied":
                raise PermissionError(13, "Permission denied", str(parent))
            return original_mkdir(path, *args, **kwargs)
        except OSError as exc:
            original_errors.append(exc)
            raise

    monkeypatch.setattr(Path, "lstat", missing_target)
    monkeypatch.setattr(Path, "mkdir", create_parent)
    with pytest.raises(CliError) as error:
        create_project(root, sync=False)
    assert error.value.code == "PROJECT_PATH_INVALID"
    assert "路径" in error.value.hint
    assert "空目录" in error.value.hint
    assert error.value.details == {"path": str(parent), "error": str(original_errors[-1])}
    assert error.value.__cause__ is original_errors[-1]
    if failure == "file_parent":
        assert parent.read_text() == "original"
    else:
        assert list(parent.iterdir()) == []


@pytest.mark.parametrize("existing", [False, True])
def test_generation_failure_preserves_original_directory(tmp_path, monkeypatch, existing):
    root = tmp_path / "bot"
    if existing:
        root.mkdir()
    original_write_marker = project_module.write_marker

    def fail_generation(project, *, directory=None):
        original_write_marker(project, directory=directory)
        raise OSError("generation failed")

    monkeypatch.setattr(project_module, "write_marker", fail_generation)
    with pytest.raises(OSError, match="generation failed"):
        create_project(root, sync=False)
    assert root.exists() == existing
    if existing:
        assert not list(root.iterdir())
    assert not list(tmp_path.glob(".neo-new-*"))


@pytest.mark.parametrize("existing", [False, True])
def test_partial_publish_failure_only_removes_new_directory(tmp_path, monkeypatch, existing):
    root = tmp_path / "bot"
    if existing:
        root.mkdir()

    def fail_copy(source, output):
        output.write(source.read(2))
        raise OSError("partial copy failed")

    monkeypatch.setattr(project_module.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="partial copy failed"):
        create_project(root, sync=False)
    assert root.exists() == existing
    if existing:
        assert not list(root.iterdir())


@pytest.mark.parametrize("replacement", ["file", "directory", "symlink"])
def test_target_changed_during_generation_is_preserved(tmp_path, monkeypatch, replacement):
    root = tmp_path / "bot"
    root.mkdir()
    original_write_marker = project_module.write_marker

    def change_target(project, *, directory=None):
        original_write_marker(project, directory=directory)
        root.rmdir()
        if replacement == "file":
            root.write_text("arrived during generation")
        elif replacement == "directory":
            root.mkdir()
            (root / "keep").write_text("arrived during generation")
        else:
            root.symlink_to(tmp_path / "external", target_is_directory=True)

    monkeypatch.setattr(project_module, "write_marker", change_target)
    with pytest.raises(CliError):
        create_project(root, sync=False)
    if replacement == "file":
        assert root.read_text() == "arrived during generation"
    elif replacement == "directory":
        assert (root / "keep").read_text() == "arrived during generation"
        assert list(root.iterdir()) == [root / "keep"]
    else:
        assert root.is_symlink()
        assert not (tmp_path / "external").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_file_conflict_is_not_overwritten_or_deleted(tmp_path, monkeypatch, existing):
    root = tmp_path / "bot"
    if existing:
        root.mkdir()
    original_copy = project_module.shutil.copyfileobj

    def add_conflicting_file(source, output):
        original_copy(source, output)
        if Path(source.name).name == ".env":
            (root / "pyproject.toml").write_text("user content")

    monkeypatch.setattr(project_module.shutil, "copyfileobj", add_conflicting_file)
    with pytest.raises(CliError, match="创建期间发生变化"):
        create_project(root, sync=False)
    assert (root / "pyproject.toml").read_text() == "user content"
    assert list(root.iterdir()) == [root / "pyproject.toml"]


@pytest.mark.parametrize("existing", [False, True])
def test_publish_failure_keeps_concurrent_nested_file(tmp_path, monkeypatch, existing):
    root = tmp_path / "bot"
    if existing:
        root.mkdir()
    original_copy = project_module.shutil.copyfileobj

    def fail_with_user_file(source, output):
        original_copy(source, output)
        if Path(source.name).name == "pyproject.toml":
            (root / "data/plugins/keep").write_text("user content")
            raise OSError("publish failed")

    monkeypatch.setattr(project_module.shutil, "copyfileobj", fail_with_user_file)
    with pytest.raises(OSError, match="publish failed"):
        create_project(root, sync=False)
    assert (root / "data/plugins/keep").read_text() == "user content"
    assert list(root.iterdir()) == [root / "data"]
    assert list((root / "data/plugins").iterdir()) == [root / "data/plugins/keep"]


def test_marker_conflict_at_end_of_publish_is_preserved(tmp_path, monkeypatch):
    root = tmp_path / "bot"
    original_copy = project_module.shutil.copyfileobj

    def add_marker(source, output):
        original_copy(source, output)
        if Path(source.name).name == "pyproject.toml":
            (root / ".neobot/project.json").write_text("user marker")

    monkeypatch.setattr(project_module.shutil, "copyfileobj", add_marker)
    with pytest.raises(CliError, match="创建期间发生变化"):
        create_project(root, sync=False)
    assert (root / ".neobot/project.json").read_text() == "user marker"
    assert list(root.iterdir()) == [root / ".neobot"]


@pytest.mark.parametrize("replace", [False, True])
def test_publish_failure_preserves_user_edit_to_generated_file(tmp_path, monkeypatch, replace):
    root = tmp_path / "bot"
    original_copy = project_module.shutil.copyfileobj

    def edit_created_file(source, output):
        original_copy(source, output)
        if Path(source.name).name == "pyproject.toml":
            if replace:
                (root / ".env").rename(tmp_path / "retired-env")
            (root / ".env").write_text("user content")
            raise OSError("publish failed")

    monkeypatch.setattr(project_module.shutil, "copyfileobj", edit_created_file)
    with pytest.raises(OSError, match="publish failed"):
        create_project(root, sync=False)
    assert (root / ".env").read_text() == "user content"
    assert list(root.iterdir()) == [root / ".env"]


@pytest.mark.parametrize("symlink", [False, True])
def test_directory_replaced_during_publish_is_preserved(tmp_path, monkeypatch, symlink):
    root = tmp_path / "bot"
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep").write_text("external content")
    original_copy = project_module.shutil.copyfileobj

    def replace_created_directory(source, output):
        original_copy(source, output)
        if Path(source.name).name == "pyproject.toml":
            (root / "data").rename(tmp_path / "retired-data")
            if symlink:
                (root / "data").symlink_to(external, target_is_directory=True)
            else:
                (root / "data").mkdir()
                (root / "data/keep").write_text("user content")

    monkeypatch.setattr(project_module.shutil, "copyfileobj", replace_created_directory)
    with pytest.raises(CliError, match="创建期间发生变化"):
        create_project(root, sync=False)
    assert (external / "keep").read_text() == "external content"
    assert list(external.iterdir()) == [external / "keep"]
    assert (root / "data/keep").read_text() == ("external content" if symlink else "user content")
    assert list(root.iterdir()) == [root / "data"]


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
