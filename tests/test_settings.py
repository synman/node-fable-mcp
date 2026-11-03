"""node_fable.settings: environment, then the config file, then the caller's default.

The config file lives outside the repository, so every machine-specific value is read
here and none is written in the source. A file that cannot be used raises instead of
falling back, because a default could point a caller at the wrong command or account.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SETTINGS = Path(__file__).resolve().parent.parent / "services" / "node_fable" / "settings.py"


def _load():
    spec = importlib.util.spec_from_file_location("_settings_under_test", SETTINGS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("NODE_FABLE_CONFIG", str(path))
    monkeypatch.delenv("NODE_FABLE_THING", raising=False)
    return path


def test_the_default_is_used_when_nothing_is_set(cfg):
    assert _load().get("thing", "fallback") == "fallback"
    assert _load().get("thing") is None


def test_the_config_file_beats_the_default(cfg):
    cfg.write_text(json.dumps({"thing": "from-file"}))
    assert _load().get("thing", "fallback") == "from-file"


def test_the_environment_beats_the_config_file(cfg, monkeypatch):
    cfg.write_text(json.dumps({"thing": "from-file"}))
    monkeypatch.setenv("NODE_FABLE_THING", "from-env")
    assert _load().get("thing", "fallback") == "from-env"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_environment_value_means_not_set(cfg, monkeypatch, blank):
    cfg.write_text(json.dumps({"thing": "from-file"}))
    monkeypatch.setenv("NODE_FABLE_THING", blank)
    assert _load().get("thing", "fallback") == "from-file"
    cfg.write_text(json.dumps({"thing": blank}))
    assert _load().get("thing", "fallback") == "fallback"


def test_a_value_is_stripped(cfg, monkeypatch):
    monkeypatch.setenv("NODE_FABLE_THING", "  padded  ")
    assert _load().get("thing") == "padded"


def test_a_missing_file_is_empty_not_an_error(cfg):
    assert not cfg.exists()
    assert _load().get("thing", "x") == "x"


@pytest.mark.parametrize("body", ["{not json", "[1]", '"text"', '{"thing": 3}', '{"thing": null}', '{"a": ["b"]}'])
def test_an_unusable_file_raises_and_names_the_file(cfg, body):
    cfg.write_text(body)
    mod = _load()
    with pytest.raises(mod.SettingsError) as exc:
        mod.get("thing", "fallback")
    assert str(cfg) in str(exc.value)


def test_a_directory_where_the_file_should_be_raises(cfg):
    cfg.mkdir()
    mod = _load()
    with pytest.raises(mod.SettingsError):
        mod.get("thing")


def test_get_path_expands_a_leading_tilde(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg.write_text(json.dumps({"where": "~/x/y"}))
    assert _load().get_path("where", "/unused") == tmp_path / "x" / "y"
    assert _load().get_path("absent", "~/d") == tmp_path / "d"


def test_the_config_file_location_follows_the_config_variable_then_xdg_then_home(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setenv("NODE_FABLE_CONFIG", str(tmp_path / "explicit.json"))
    assert mod.config_path() == tmp_path / "explicit.json"
    monkeypatch.delenv("NODE_FABLE_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert mod.config_path() == tmp_path / "xdg" / "node-fable-mcp" / "config.json"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mod.config_path() == tmp_path / ".config" / "node-fable-mcp" / "config.json"


def test_the_environment_variable_of_a_key_is_the_prefix_plus_the_upper_case_key():
    assert _load().env_name("child_env_set") == "NODE_FABLE_CHILD_ENV_SET"


# --- lists and maps are JSON strings, parsed loudly -----------------------------------------------


def test_a_json_list_setting_parses_from_the_environment_and_the_file(cfg, monkeypatch):
    mod = _load()
    assert mod.get_json_list("thing") is None
    cfg.write_text(json.dumps({"thing": '["a", "b"]'}))
    assert mod.get_json_list("thing") == ["a", "b"]
    monkeypatch.setenv("NODE_FABLE_THING", '["c"]')
    assert mod.get_json_list("thing") == ["c"]


@pytest.mark.parametrize("value", ["not json", '{"a": "b"}', "[1, 2]", '["a", null]', '"text"'])
def test_a_json_list_setting_that_is_not_a_list_of_strings_raises(cfg, monkeypatch, value):
    monkeypatch.setenv("NODE_FABLE_THING", value)
    mod = _load()
    with pytest.raises(mod.SettingsError) as exc:
        mod.get_json_list("thing")
    assert "thing" in str(exc.value)


def test_a_json_map_setting_parses_and_rejects_the_wrong_shape(cfg, monkeypatch):
    mod = _load()
    assert mod.get_json_map("thing") is None
    monkeypatch.setenv("NODE_FABLE_THING", '{"k": "v"}')
    assert mod.get_json_map("thing") == {"k": "v"}
    for bad in ("not json", '["k"]', '{"k": 1}', '{"k": null}'):
        monkeypatch.setenv("NODE_FABLE_THING", bad)
        with pytest.raises(mod.SettingsError):
            mod.get_json_map("thing")


def test_the_module_is_a_leaf():
    import ast

    tree = ast.parse(SETTINGS.read_text())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"json", "os", "pathlib", "__future__"}


def test_importing_the_module_reads_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NODE_FABLE_CONFIG", str(tmp_path / "would-raise"))
    (tmp_path / "would-raise").write_text("{broken")
    _load()  # a read at import would raise SettingsError here
