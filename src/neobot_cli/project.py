"""单实例目录识别、创建与接管。"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import CliError
from .host_release import HOST_PACKAGES, HOST_REPOSITORY, HOST_REVISION

MARKER = ".neobot/project.json"


def _default_path() -> Path:
    override = os.getenv("NEOBOT_CLI_CONFIG_DIR")
    if override:
        return Path(override) / "default.json"
    base = (
        Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming")))
        if os.name == "nt"
        else Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    )
    return base / "neobot-cli/default.json"


def remember_project(project: Workspace) -> None:
    path = _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"root": str(project.root)}, ensure_ascii=False) + "\n", encoding="utf-8")


def default_project() -> Workspace | None:
    try:
        document = json.loads(_default_path().read_text(encoding="utf-8"))
        return discover_project(document["root"])
    except (FileNotFoundError, ValueError, TypeError, KeyError, CliError):
        return None


@dataclass(frozen=True)
class Workspace:
    root: Path
    data_dir: Path
    plugin_dir: Path
    env_file: Path

    def to_dict(self) -> dict:
        return {key: str(getattr(self, key)) for key in ("root", "data_dir", "plugin_dir", "env_file")}


def uv_command() -> str:
    command = shutil.which("uv")
    if not command:
        raise CliError("UV_REQUIRED", "找不到 uv", hint="先按 https://docs.astral.sh/uv/ 安装 uv。")
    return command


def run_uv(project: Workspace, arguments: list[str], *, timeout: float = 600) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment.update(NEOBOT_DATA_DIR=str(project.data_dir), NEOBOT_ENV_FILE=str(project.env_file))
    # Never inherit the tool's activated environment into the user's instance.
    environment.pop("VIRTUAL_ENV", None)
    try:
        return subprocess.run(
            [uv_command(), *arguments],
            cwd=project.root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CliError("PROCESS_TIMEOUT", "操作超时，请查看实例日志后重试。") from exc


def write_marker(project: Workspace, *, directory: Path | None = None) -> None:
    marker = (directory if directory is not None else project.root) / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    document = {"schema_version": 1, **project.to_dict()}
    with tempfile.NamedTemporaryFile("w", dir=marker.parent, encoding="utf-8", delete=False) as output:
        temporary = Path(output.name)
        json.dump(document, output, ensure_ascii=False, indent=2)
        output.write("\n")
    try:
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def discover_project(path: str | Path | None = None) -> Workspace | None:
    current = Path(path or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for root in (current, *current.parents):
        marker = root / MARKER
        if not marker.is_file():
            continue
        try:
            document = json.loads(marker.read_text(encoding="utf-8"))
            if document.get("schema_version") != 1:
                raise ValueError("unsupported schema")
            workspace = Workspace(
                root, *(Path(document[key]).resolve() for key in ("data_dir", "plugin_dir", "env_file"))
            )
            if Path(document["root"]).resolve() != root:
                raise ValueError("instance moved")
            return workspace
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise CliError("PROJECT_INVALID", "实例记录无效或目录已移动，请重新接管该目录。") from exc
    return None


def adopt_project(path: str | Path) -> Workspace:
    root = Path(path).expanduser().resolve()
    if not (root / "pyproject.toml").is_file():
        raise CliError("PROJECT_INVALID", "所选目录不是 uv 工程：缺少 pyproject.toml。")
    # Source checkouts use app/data; generated deployments use data.
    data = root / "app/data" if (root / "app/src/neobot_app").is_dir() else root / "data"
    env = root / "app/.env" if (root / "app/src/neobot_app").is_dir() else root / ".env"
    config = data / "config.toml"
    relative = "./plugins"
    try:
        if config.is_file():
            relative = tomllib.loads(config.read_text(encoding="utf-8")).get("plugins", {}).get("dir", relative)
        plugin_dir = Path(relative)
    except (OSError, ValueError, TypeError) as exc:
        raise CliError("CONFIG_INVALID", "无法读取实例的插件目录配置。") from exc
    if not plugin_dir.is_absolute():
        plugin_dir = data / plugin_dir
    project = Workspace(root, data.resolve(), plugin_dir.resolve(), env.resolve())
    write_marker(project)
    return project


def _creation_target(root: Path) -> os.stat_result | None:
    try:
        state = root.lstat()
        if not stat.S_ISDIR(state.st_mode):
            kind = "符号链接" if stat.S_ISLNK(state.st_mode) else "文件或其他非目录项"
            raise CliError(
                "PROJECT_EXISTS",
                f"目标路径是{kind}，不能创建工程。",
                hint="请选择真实的空目录，或输入一个尚不存在的新目录路径。",
            )
        if next(root.iterdir(), None) is not None:
            if (root / "pyproject.toml").exists() or (root / MARKER).exists():
                raise CliError(
                    "PROJECT_EXISTS",
                    "目标目录已存在工程，请使用“接入已有工程”。",
                    hint="返回首页选择“接入已有工程”，或运行 neo project adopt <工程目录>。",
                )
            raise CliError(
                "PROJECT_EXISTS",
                "目标目录已存在且非空，不能创建工程。",
                hint="请选择空目录，或在当前目录下输入一个新的子目录路径。",
            )
        return state
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CliError(
            "PROJECT_PATH_INVALID",
            "无法访问目标目录。",
            hint="请检查路径及目录权限，选择可读写的空目录或新的子目录。",
        ) from exc


def _same_entry(path: Path, expected: os.stat_result) -> bool:
    try:
        actual = path.lstat()
    except OSError:
        return False
    return (actual.st_dev, actual.st_ino, actual.st_mode) == (
        expected.st_dev,
        expected.st_ino,
        expected.st_mode,
    )


def _publish_project(staging: Path, root: Path, original: os.stat_result | None) -> None:
    changed = CliError(
        "PROJECT_EXISTS",
        "目标目录在创建期间发生变化，已保留其他内容。",
        hint="请检查该目录，选择空目录或新的子目录后重新创建。",
    )
    directories: dict[Path, os.stat_result] = {}
    files: list[tuple[Path, os.stat_result, Path, bool]] = []

    def parents_intact(path: Path) -> bool:
        return all(_same_entry(parent, state) for parent, state in directories.items() if parent in path.parents)

    try:
        current = _creation_target(root)
        if original is None:
            if current is not None:
                raise changed
            root.mkdir()
        elif current is None or not _same_entry(root, original):
            raise changed
        directories[root] = root.lstat()
        # The marker becomes visible only after all other project files are ready.
        for item in sorted(staging.rglob("*"), key=lambda path: (path == staging / MARKER, path)):
            target = root / item.relative_to(staging)
            if not all(_same_entry(directory, state) for directory, state in directories.items()):
                raise changed
            if item.is_dir():
                target.mkdir()
                directories[target] = target.lstat()
            else:
                # Exclusive creation also rejects files or links arriving after the empty check.
                with item.open("rb") as source, target.open("xb") as output:
                    state = os.fstat(output.fileno())
                    files.append((target, state, item, False))
                    shutil.copyfileobj(source, output)
                files[-1] = (target, state, item, True)
    except Exception as exc:
        # Only undo entries we created. Never recurse through user additions or replaced directories.
        for target, state, item, complete in reversed(files):
            if parents_intact(target) and _same_entry(target, state):
                try:
                    actual, expected = target.read_bytes(), item.read_bytes()
                    # Preserve user edits to our files too, even if the inode has not changed.
                    if actual == expected or (not complete and expected.startswith(actual)):
                        target.unlink(missing_ok=True)
                except OSError:
                    pass
        for target, state in reversed(directories.items()):
            if target == root and original is not None:
                continue
            if parents_intact(target) and _same_entry(target, state):
                try:
                    target.rmdir()
                except OSError:
                    pass  # A concurrent addition must keep its containing directory alive.
        if isinstance(exc, FileExistsError):
            raise changed from exc
        raise


def create_project(path: str | Path, *, source: Path | None = None, sync: bool = True) -> Workspace:
    root = Path(path).expanduser().absolute()
    original = _creation_target(root)
    if source is not None:
        source = Path(source).resolve()
        if not (source / "app/pyproject.toml").is_file():
            raise CliError("SOURCE_INVALID", "NeoBot 源码目录缺少 app/pyproject.toml。")
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError(
            "PROJECT_PATH_INVALID",
            "无法创建目标目录的父目录。",
            hint="请检查路径及目录权限，选择可读写的空目录或新的子目录。",
            details={"path": str(root.parent), "error": str(exc)},
        ) from exc
    # Generate privately, then publish without overwriting an existing target.
    with tempfile.TemporaryDirectory(prefix=".neo-new-", dir=root.parent) as directory:
        staging = Path(directory)
        (staging / "data/plugins").mkdir(parents=True)
        content = (
            '[project]\nname = "my-neobot"\nversion = "0.1.0"\n'
            'requires-python = ">=3.13"\ndependencies = ["neobot-app>=2.0.2a14"]\n'
            '\n[dependency-groups]\ndev = ["pytest>=9,<10", "pytest-asyncio>=1,<2"]\n'
        )
        if source:
            content += "\n[tool.uv.sources]\n"
            packages = {"neobot-app": source / "app"}
            for package in sorted((source / "packages").glob("*/pyproject.toml")):
                metadata = tomllib.loads(package.read_text(encoding="utf-8"))
                packages[metadata["project"]["name"]] = package.parent
            for name, location in packages.items():
                content += f"{name} = {{ path = {json.dumps(str(location))}, editable = true }}\n"
        else:
            content += "\n[tool.uv.sources]\n"
            for name, directory in HOST_PACKAGES.items():
                content += (
                    f"{name} = {{ git = {json.dumps(HOST_REPOSITORY)}, rev = {json.dumps(HOST_REVISION)}, "
                    f"subdirectory = {json.dumps(directory)} }}\n"
                )
        (staging / "pyproject.toml").write_text(content, encoding="utf-8")
        (staging / ".python-version").write_text("3.13\n", encoding="utf-8")
        (staging / ".env").write_text("# 在现有面板中配置平台连接与模型。\n", encoding="utf-8")
        (staging / ".gitignore").write_text(".venv/\n.env\ndata/\n.neobot/\n", encoding="utf-8")
        (staging / "README.md").write_text(
            "# 我的 NeoBot\n\n运行 `neo` 打开工作台；`neo start` 启动当前实例。\n"
            "插件在 data/plugins/ 创建，配置和数据属于同一个实例。\n",
            encoding="utf-8",
        )
        project = Workspace(root.resolve(), (root / "data").resolve(), (root / "data/plugins").resolve(), root / ".env")
        write_marker(project, directory=staging)
        _publish_project(staging, root, original)
    if sync:
        result = run_uv(project, ["sync", "--project", str(project.root)])
        if result.returncode:
            raise CliError(
                "SYNC_FAILED",
                "工程已创建，但环境准备失败。可修正网络或依赖后执行 neo sync。",
                details={"root": str(root), "stderr": result.stderr[-4000:]},
            )
    return project
