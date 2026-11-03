"""Nothing environment-specific ships in the code, so a node that configures nothing
refuses cleanly and spends nothing.

Every case here runs with no setting at all (the autouse fixture in conftest.py clears
them) or with one setting removed from the fake configuration, and asserts that the
launch function is never reached. The launch function is replaced by a recorder that
fails the test if it is called: a real consultation spends money, so no test may run one.
"""
import json
import sys
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import account_snapshot, accounts, config, mcp_grant, runner  # noqa: E402
from node_fable.errors import FableError  # noqa: E402


@pytest.fixture
def no_launch(monkeypatch):
    calls = []

    def _spawn(*args, **kwargs):
        calls.append(args)
        raise AssertionError("a launch was attempted; nothing may be spent here")

    monkeypatch.setattr(runner, "_spawn", _spawn)
    return calls


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("NODE_FABLE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NODE_FABLE_ACCOUNTS", "a@example.com")
    return tmp_path / "state"


def test_a_consult_on_an_unconfigured_node_refuses_and_spends_nothing(state, no_launch):
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief="a brief")
    assert ei.value.error_class == "not_configured"
    assert "NODE_FABLE_MODELS" in ei.value.message
    assert no_launch == []
    assert not (state / "call-state.json").exists(), "a refusal must record nothing"


@pytest.mark.parametrize(
    "missing",
    ["command", "command_args", "system_prompt_args", "mcp_config_template", "settings_template"],
)
def test_each_required_launch_setting_is_refused_by_name_before_anything_is_recorded(
    missing, fake_cli, state, no_launch, monkeypatch
):
    """The direct launch path (a non-default model) needs the command, not the switcher."""
    monkeypatch.delenv("NODE_FABLE_" + missing.upper())
    monkeypatch.setattr(runner, "_active_account_email", lambda snapshot=None: "a@example.com")
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief="a brief", model="fallback-test-1")
    assert ei.value.error_class == "not_configured"
    assert "NODE_FABLE_" + missing.upper() in ei.value.message
    assert no_launch == []
    assert not (state / "call-state.json").exists()


@pytest.mark.parametrize("missing", ["excluded_servers", "denied_tools", "git_write_deny_tools"])
def test_a_safety_list_that_is_unset_refuses_the_call_and_an_empty_one_does_not(
    missing, fake_cli, state, no_launch, monkeypatch
):
    """An unset safety list must never silently mean none: it refuses before anything is
    recorded. The explicit empty list is the operator's own choice and is accepted."""
    monkeypatch.setattr(runner, "_active_account_email", lambda snapshot=None: "a@example.com")
    monkeypatch.delenv("NODE_FABLE_" + missing.upper())
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief="a brief", model="fallback-test-1")
    assert ei.value.error_class == "not_configured"
    assert "NODE_FABLE_" + missing.upper() in ei.value.message
    assert no_launch == []
    assert not (state / "call-state.json").exists()
    monkeypatch.setenv("NODE_FABLE_" + missing.upper(), "[]")
    runner.require_launch_configured(True)  # an explicit empty list passes the check


def test_git_write_override_is_the_one_call_that_needs_no_deny_list(fake_cli, monkeypatch):
    monkeypatch.delenv("NODE_FABLE_GIT_WRITE_DENY_TOOLS")
    runner.require_launch_configured(True, git_write_override=True)
    argv = runner.build_direct_argv(
        model="fallback-test-1", effort="high", budget_usd="5.00", confirmed=False,
        add_dirs=[], system_prompt_file="/scratch/p.md", git_write_override=True,
    )
    assert "--disallowedTools" not in argv
    with pytest.raises(FableError) as ei:
        runner.build_direct_argv(
            model="fallback-test-1", effort="high", budget_usd="5.00", confirmed=False,
            add_dirs=[], system_prompt_file="/scratch/p.md",
        )
    assert ei.value.error_class == "not_configured"


def test_forbidden_roots_unset_refuses_a_granted_path_and_empty_allows_it(fake_cli, tmp_path, monkeypatch):
    monkeypatch.delenv("NODE_FABLE_FORBIDDEN_ROOTS")
    with pytest.raises(FableError) as ei:
        runner.validate_grant_path(str(tmp_path), "material")
    assert ei.value.error_class == "not_configured"
    monkeypatch.setenv("NODE_FABLE_FORBIDDEN_ROOTS", "[]")
    assert runner.validate_grant_path(str(tmp_path), "material") == str(tmp_path.resolve())


def test_the_switcher_path_needs_the_switcher_command(fake_cli, state, no_launch, monkeypatch):
    monkeypatch.delenv("NODE_FABLE_SWITCHER_COMMAND")
    monkeypatch.setattr(
        accounts, "account_rows", lambda snapshot=None, cooldowns=None: [
            {"email": "a@example.com", "number": "1", "is_active": False, "windows": [],
             "five_h_avail": 90.0, "seven_d_avail": 90.0, "fable_headroom": 90.0, "gate_ok": True,
             "usage_age_s": 1.0, "in_cooldown": False, "cooldown_until": None, "disabled": False}
        ],
    )
    with pytest.raises(FableError) as ei:
        runner.consult(role="plan", brief="a brief")
    assert ei.value.error_class == "not_configured"
    assert "NODE_FABLE_SWITCHER_COMMAND" in ei.value.message
    assert no_launch == []


def test_a_group_template_missing_at_build_time_is_refused_not_dropped(fake_cli, monkeypatch):
    """Dropping the deny group would fail open, so a missing group is an error."""
    monkeypatch.delenv("NODE_FABLE_DISALLOWED_TOOLS_ARGS")
    with pytest.raises(FableError) as ei:
        runner.build_direct_argv(
            model="fallback-test-1", effort="high", budget_usd="5.00", confirmed=False,
            add_dirs=[], system_prompt_file="/scratch/p.md",
        )
    assert ei.value.error_class == "not_configured"


def test_a_template_token_the_launch_does_not_supply_is_refused(fake_cli, monkeypatch):
    monkeypatch.setenv("NODE_FABLE_COMMAND_ARGS", json.dumps(["--model", "{unknown_token}"]))
    with pytest.raises(FableError) as ei:
        runner.build_direct_argv(
            model="fallback-test-1", effort="high", budget_usd="5.00", confirmed=False,
            add_dirs=[], system_prompt_file="/scratch/p.md",
        )
    assert ei.value.error_class == "invalid_setting"
    assert "unknown_token" in ei.value.message


def test_a_list_token_expands_to_one_element_per_value():
    out = runner.render_template(
        ["--x", "{tools}", "--y", "{one}-{two}"], {"one": "1", "two": "2"}, {"tools": ["a", "b"]}, setting="s"
    )
    assert out == ["--x", "a", "b", "--y", "1-2"]


def test_a_bad_setting_value_is_a_structured_error_not_a_traceback(state, monkeypatch):
    monkeypatch.setenv("NODE_FABLE_MODELS", "not json")
    with pytest.raises(FableError) as ei:
        runner.validate_model(None)
    assert ei.value.error_class == "invalid_setting"


def test_the_default_model_is_the_setting_else_the_first_listed(fake_cli, monkeypatch):
    assert config.default_model() == "fable-test-1"
    monkeypatch.setenv("NODE_FABLE_DEFAULT_MODEL", "fallback-test-2")
    assert config.default_model() == "fallback-test-2"
    assert runner.direct_invoke_models() == ("fable-test-1", "fallback-test-1")


def test_headroom_needs_the_accounts_command(state):
    with pytest.raises(FableError) as ei:
        accounts.account_rows()
    assert ei.value.error_class == "not_configured"
    assert "NODE_FABLE_ACCOUNTS_COMMAND" in ei.value.message


def test_no_generated_payload_exists_without_its_template():
    with pytest.raises(FableError) as ei:
        mcp_grant.settings_json({"mcpServers": {}, "deny": []})
    assert ei.value.error_class == "not_configured"


# --- the accounts snapshot comes from a configured command, parsed strictly -------------------------

_GOOD = {
    "accounts": [
        {
            "email": "alpha@example.com", "number": 1, "is_active": True, "disabled": False,
            "usage_age_s": 12.5,
            "windows": [
                {"label": "5h", "pct_used": 10, "resets_at": "r1"},
                {"label": "7d", "pct_used": 20.5, "resets_at": None},
            ],
        },
        {"email": "beta@example.com"},
    ]
}


def test_a_snapshot_parses_into_accounts_and_windows():
    snap = account_snapshot.parse_snapshot(json.dumps(_GOOD))
    first, second = snap.accounts
    assert (first.email, first.number, first.is_active, first.disabled) == ("alpha@example.com", "1", True, False)
    assert first.windows == (("5h", 10.0, "r1"), ("7d", 20.5, None))
    assert first.usage_age_s == 12.5
    assert (second.is_active, second.disabled, second.windows, second.usage_age_s) == (False, False, (), None)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"accounts": "x"}',
        '{"accounts": null}',
        '{"accounts": 5}',
        '{"accounts": [{"email": "a@example.com", "windows": [{"label": 5, "pct_used": 1}]}]}',
        '{"accounts": [{"email": ""}]}',
        '{"accounts": [{"no_email": 1}]}',
        '{"accounts": [{"email": "a@example.com", "windows": "x"}]}',
        '{"accounts": [{"email": "a@example.com", "windows": [{"label": "5h"}]}]}',
        '{"accounts": [{"email": "a@example.com", "windows": [{"label": "5h", "pct_used": true}]}]}',
        '{"accounts": [{"email": "a@example.com", "usage_age_s": "soon"}]}',
    ],
)
def test_an_unusable_snapshot_is_a_structured_error(text):
    with pytest.raises(FableError) as ei:
        account_snapshot.parse_snapshot(text)
    assert ei.value.error_class == "missing_dependency"


def test_the_accounts_command_runs_with_this_process_environment(fake_cli, tmp_path, monkeypatch):
    script = tmp_path / "accounts.py"
    script.write_text(
        "import json, os\n"
        "who = os.environ.get('FAKE_ACCOUNTS_USER', 'nobody')\n"
        "print(json.dumps({'accounts': [{'email': who + '@example.com'}]}))\n"
    )
    monkeypatch.setenv("NODE_FABLE_ACCOUNTS_COMMAND", json.dumps([sys.executable, str(script)]))
    monkeypatch.setenv("FAKE_ACCOUNTS_USER", "alpha")
    snap = account_snapshot.accounts_snapshot()
    assert [a.email for a in snap.accounts] == ["alpha@example.com"]


_VALID = "import json; print(json.dumps({'accounts': []}))"


@pytest.mark.parametrize(
    "code",
    [
        "import sys; sys.exit(3)",
        # a non-zero exit is refused even when stdout is a perfectly good snapshot
        _VALID + "; import sys; sys.exit(3)",
        "print('not json')",
        # a command slower than the limit is refused even though it would have printed a good snapshot
        "import time; time.sleep(5); " + _VALID,
    ],
)
def test_a_failing_accounts_command_is_a_structured_error(fake_cli, tmp_path, monkeypatch, code):
    monkeypatch.setenv("NODE_FABLE_ACCOUNTS_COMMAND", json.dumps([sys.executable, "-c", code]))
    monkeypatch.setenv("NODE_FABLE_ACCOUNTS_TIMEOUT_S", "1")
    with pytest.raises(FableError) as ei:
        account_snapshot.accounts_snapshot()
    assert ei.value.error_class == "missing_dependency"


def test_an_accounts_command_that_cannot_start_is_a_structured_error(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("NODE_FABLE_ACCOUNTS_COMMAND", json.dumps([str(tmp_path / "absent-command")]))
    with pytest.raises(FableError) as ei:
        account_snapshot.accounts_snapshot()
    assert ei.value.error_class == "missing_dependency"
