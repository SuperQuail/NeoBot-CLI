import pytest

from neobot_cli.templates import create_plugin, list_templates


def test_creates_three_different_local_templates(tmp_path):
    templates = list_templates()
    assert {item["id"] for item in templates} == {"command", "event", "page"}
    for template in templates:
        path = create_plugin(tmp_path / "plugins", f"sample_{template['id']}", template["id"])
        assert (path / "plugin.toml").is_file()
        assert (path / "tests/test_plugin.py").is_file()
        assert "__PLUGIN_NAME__" not in (path / "__init__.py").read_text()
        assert not (path / ".venv").exists()
        compile((path / "__init__.py").read_text(), str(path / "__init__.py"), "exec")
    assert "@plugin.command" in (tmp_path / "plugins/sample_command/__init__.py").read_text()
    assert "@plugin.message" in (tmp_path / "plugins/sample_event/__init__.py").read_text()
    assert (tmp_path / "plugins/sample_page/web/app.js").is_file()


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "", "has space", "has-dash", "a__b", "con", "lpt1", "hello."]
)
def test_rejects_nonportable_or_escaping_names(tmp_path, name):
    with pytest.raises(ValueError):
        create_plugin(tmp_path / "plugins", name)
    assert list(tmp_path.iterdir()) == []


def test_never_overwrites_existing_plugin_or_symlink(tmp_path):
    path = create_plugin(tmp_path, "hello")
    source = path / "__init__.py"
    source.write_text("keep me")
    with pytest.raises(ValueError):
        create_plugin(tmp_path, "hello", "page")
    assert source.read_text() == "keep me"
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this environment cannot create symlinks")
    with pytest.raises(ValueError):
        create_plugin(link, "escaped")
    assert not (outside / "escaped").exists()


def test_unknown_template_does_not_create_directory(tmp_path):
    with pytest.raises(ValueError, match="未知插件模板"):
        create_plugin(tmp_path / "plugins", "hello", "missing")
    assert not (tmp_path / "plugins").exists()


def test_template_descriptors_are_not_mutable_registry_state():
    first = list_templates()
    first[0]["id"] = "modified"
    assert list_templates()[0]["id"] == "command"
