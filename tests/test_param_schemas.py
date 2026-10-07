"""Every parameter is described in the live input schema, and the published
argument shape did not move.

Reads the tool list a client actually receives (``list_tools()``), per the
mcp-builder skill's conformity check (SKILL.md 3.3 item 6): a docstring `Args:`
block never reaches the input schema, so each description sits on its parameter
via `Annotated[..., Field(description=...)]`. The shape pins below were captured
from the live schema BEFORE that move, so the move cannot have changed a
contract: names, JSON type, default, required set.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))
_spec = importlib.util.spec_from_file_location("nfm_param_schemas", _SERVICES / "node-fable-mcp.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

EXPECTED_PARAMETERS = 11  # a change here is a deliberate, reviewed act

# tool -> {parameter: (json type, default or None, required)}; a nullable string or
# array is published as anyOf [T, null] with default null.
PUBLISHED_SHAPE = {
    "fable_consult": {
        "role": ("string", None, True),
        "brief": ("string", None, True),
        "material": ("array|null", None, False),
        "confirmed": ("boolean", False, False),
        "session_id": ("string|null", None, False),
        "model": ("string|null", None, False),
        "effort": ("string|null", None, False),
        "scope": ("string|null", None, False),
        "target_repo": ("string|null", None, False),
        "git_write_override": ("boolean", False, False),
        "alias_internal_names": ("boolean", False, False),
    },
    "fable_headroom": {},
}


def _live_tools():
    return asyncio.run(server.mcp.list_tools())


def _json_type(prop: dict) -> str:
    if "anyOf" in prop:
        return "|".join(branch["type"] for branch in prop["anyOf"])
    return prop["type"]


def test_parameter_count_is_pinned():
    total = sum(len(t.inputSchema.get("properties", {})) for t in _live_tools())
    assert total == EXPECTED_PARAMETERS


def test_every_parameter_has_a_schema_description():
    walked = 0
    for tool in _live_tools():
        for name, prop in tool.inputSchema.get("properties", {}).items():
            walked += 1
            assert (prop.get("description") or "").strip(), (tool.name, name)
    assert walked == EXPECTED_PARAMETERS, "the probe walked the wrong number of parameters"


def test_published_argument_shape_unchanged():
    tools = {t.name: t for t in _live_tools()}
    assert set(tools) == set(PUBLISHED_SHAPE)
    for name, expected in PUBLISHED_SHAPE.items():
        schema = tools[name].inputSchema
        props = schema.get("properties", {})
        assert set(props) == set(expected), name
        required = set(schema.get("required", []))
        assert required == {p for p, (_t, _d, req) in expected.items() if req}, name
        for param, (json_type, default, is_required) in expected.items():
            assert _json_type(props[param]) == json_type, (name, param)
            if is_required:
                assert "default" not in props[param], (name, param)
            else:
                assert props[param]["default"] == default, (name, param)


def test_no_description_carries_an_args_block():
    """One fact, one writer: the parameter text lives in the schema, not twice."""
    for tool in _live_tools():
        assert not re.search(r"^\s*Args:\s*$", tool.description or "", re.M), tool.name


def test_description_cap_still_holds_after_the_args_move():
    for tool in _live_tools():
        assert len(tool.description or "") <= 2048, tool.name


def test_consult_returns_line_names_the_six_failure_classes():
    """A caller branches on these, so they must reach the description."""
    consult = {t.name: t for t in _live_tools()}["fable_consult"]
    for failure_class in ("no_headroom", "subprocess_error", "malformed_output",
                          "timeout", "refused", "rate_limited"):
        assert failure_class in consult.description, failure_class


def test_consult_returns_line_separates_pre_launch_refusals():
    """A caller who branches on failure_class must be told a pre-launch refusal
    (a FableError payload) carries error_class and has no failure_class at all
    (errors.py FableError.payload), or it reads that absence as an unknown failure."""
    consult = {t.name: t for t in _live_tools()}["fable_consult"]
    text = " ".join(consult.description.split())
    for name in ("secret_refused", "forbidden_path", "self_review"):
        assert name in text, name
    assert "has no failure_class" in text
    assert "carries error_class" in text
    assert len(consult.description) <= 2048
