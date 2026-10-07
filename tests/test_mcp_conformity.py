"""Conformity test for the mcp-builder skill, section 3.3 items 1 to 8.

ONE place asserts all eight rules, on the tool list a client actually receives
(``mcp.list_tools()``) and the live ``mcp.instructions``, never on decorators.
Each rule is a named check in ``violations()`` so a planted fault can be shown
to trip exactly one rule, and every rule has at least one.

Overlap, stated rather than hidden. Nothing was folded or deleted in the packet
that added this file, because the packet's mutation ceiling is one test file and
one doc file. These existing tests stay and are narrower, each pinning something
this file does not:

* ``test_published_surface.py``: the tool-name list and the per-hint annotation
  pins (rules 1, 2, 3 by their own expected values), the README-hosted pitfall
  text, the description's section names.
* ``test_param_schemas.py``: every parameter's JSON type, default and required
  flag, the parameter count, and the six failure classes in the description
  (rules 6 and 8 are redundant there; the shape pin is not).
* ``test_stdio_smoke.py``: the same description and cap checks over the real
  stdio wire (rule 5 is redundant there; the wire is not).

This file is the one that carries all eight rules, the guard derivation from the
server body, and the planted faults.

Reading a red: the planted-fault cases assert the live list is green first (the
control), so a defect in the SOURCE also turns several of them red at once, not
only ``test_live_list_conforms``. Fix the source violation and run
``test_live_list_conforms`` alone to see which rule it is; do not read the extra
planted-fault reds as separate defects.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import re
from pathlib import Path

import pytest

SERVER_PATH = Path(__file__).resolve().parent.parent / "services" / "node-fable-mcp.py"
_spec = importlib.util.spec_from_file_location("nfm_conformity_server", str(SERVER_PATH))
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
mcp = server.mcp

EXPECTED_TOOLS = 2  # rule 1: a change here is a deliberate, reviewed act (local count, no outside dependency)
GUARD = "confirmed"  # this server's write-guard: the argument a tool forwards into runner.consult
EXPECTED_GUARDED = {"fable_consult"}  # pinned so rule 4 cannot go vacuous
MAX_DESCRIPTION = 2048  # Claude Code's per-tool description cap
MAX_INSTRUCTIONS = 2048  # the same cap, applied to server instructions
PROPERTY_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")

# Declarations as at the pre-publication pin c29d5719d839, captured verbatim
# from the source of that revision, never from the live list. A mechanical pass must never turn a declared write into a
# read or move any other hint.
PRIOR = {
    "fable_consult": {
        "title": "Fable Consult",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
    "fable_headroom": {
        "title": "Fable Headroom",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

# The ONE allowed difference from the pin, made in the Annotation Truth packet
# (commit ebd54a5): fable_headroom's body calls cswap accounts_snapshot(fetch=None),
# which can GET api.anthropic.com/api/oauth/usage, so a read that can reach an
# external service is open-world. False at the pin, True now, by the owner's
# recorded decision. Anything else that differs from the pin is rule 3's finding.
CORRECTIONS = {"fable_headroom": {"openWorldHint": True}}

# Fields whose ABSENCE rule 2 owns; rule 3 judges only a changed declaration there.
RULE_2_OWNS = {"title", "readOnlyHint"}

RULES = (
    "1_count",
    "2_title_and_readonly_present",
    "3_prior_unchanged",
    "4_guarded_is_write",
    "5_description_cap",
    "6_param_description",
    "7_instructions_cap",
    "8_property_name",
)


def live_tools():
    return asyncio.run(mcp.list_tools())


def _is_tool_decorator(node: ast.expr) -> bool:
    call = node.func if isinstance(node, ast.Call) else node
    return isinstance(call, ast.Attribute) and call.attr == "tool"


def _tool_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"))
    return {
        fn.name: fn
        for fn in tree.body
        if isinstance(fn, ast.FunctionDef) and any(_is_tool_decorator(d) for d in fn.decorator_list)
    }


def body_guarded_tools() -> set[str]:
    """Tools whose BODY forwards the guard: a keyword named ``confirmed`` in any
    call, or a read of the name ``confirmed``. A hard-coded ``confirmed=True``
    counts, which is the case a schema-only reading cannot see."""
    guarded = set()
    for name, fn in _tool_functions().items():
        for node in ast.walk(fn):
            if isinstance(node, ast.keyword) and node.arg == GUARD:
                guarded.add(name)
            elif isinstance(node, ast.Name) and node.id == GUARD and isinstance(node.ctx, ast.Load):
                guarded.add(name)
    return guarded


def violations(tools, instructions, prior=PRIOR, expected=EXPECTED_TOOLS, guarded=None, corrections=CORRECTIONS):
    """Return {rule: [offenders]} for every rule that fails."""
    if guarded is None:
        guarded = body_guarded_tools()
    found: dict[str, list] = {rule: [] for rule in RULES}
    if len(tools) != expected:
        found["1_count"].append(len(tools))
    for tool in tools:
        ann = tool.annotations
        if ann is None or not (ann.title or "").strip() or ann.readOnlyHint is None:
            found["2_title_and_readonly_present"].append(tool.name)
        want = {**prior.get(tool.name, {}), **corrections.get(tool.name, {})}
        for key, value in want.items():
            live = getattr(ann, key, None) if ann is not None else None
            # An absent title or readOnlyHint is rule 2's finding; rule 3 judges the rest.
            if live is None and key in RULE_2_OWNS:
                continue
            if live != value:
                found["3_prior_unchanged"].append((tool.name, key))
        props = tool.inputSchema.get("properties", {})
        if (tool.name in guarded or GUARD in props) and not (ann is not None and ann.readOnlyHint is False):
            found["4_guarded_is_write"].append(tool.name)
        if len(tool.description or "") > MAX_DESCRIPTION:
            found["5_description_cap"].append(tool.name)
        for name, prop in props.items():
            if not (prop.get("description") or "").strip():
                found["6_param_description"].append((tool.name, name))
            if not PROPERTY_NAME.fullmatch(name):
                found["8_property_name"].append((tool.name, name))
    if len(instructions or "") > MAX_INSTRUCTIONS:
        found["7_instructions_cap"].append(len(instructions))
    return {rule: bad for rule, bad in found.items() if bad}


def test_live_list_conforms():
    assert violations(live_tools(), mcp.instructions) == {}


def test_server_under_test_is_the_sibling_one():
    """The server and its package come from this file's own tree, so a copy of the
    tree under test is the copy being measured and not some other checkout."""
    assert Path(server.__file__).resolve() == SERVER_PATH.resolve()
    assert SERVER_PATH.parent in Path(server.runner.__file__).resolve().parents


def test_scan_surface_is_not_vacuous():
    """The body scan found every live tool and the rules saw parameters."""
    live = {t.name for t in live_tools()}
    assert set(_tool_functions()) == live
    assert sum(len(t.inputSchema.get("properties", {})) for t in live_tools()) > 0


def test_guard_set_is_the_expected_one():
    """Rule 4 would be vacuous if no tool were guarded, and a drifting guard set must be a reviewed change.

    The set derived from the server body is non-empty, equals the tools whose
    schema takes the guard argument, and equals the pinned set. A tool that
    called the guard WITHOUT declaring the parameter would appear in the body
    set and not the schema set, and this equality is what catches it; one that
    both hides the parameter and forwards a hard-coded value is caught by the
    pin only.
    """
    from_body = body_guarded_tools()
    from_schema = {t.name for t in live_tools() if GUARD in t.inputSchema.get("properties", {})}
    assert from_body
    assert from_body == from_schema
    assert from_body == EXPECTED_GUARDED


def test_prior_covers_every_live_tool():
    assert set(PRIOR) == {t.name for t in live_tools()}


def test_the_one_correction_is_named_and_real():
    """A single allowed difference from the pin, and it really differs from it."""
    assert CORRECTIONS == {"fable_headroom": {"openWorldHint": True}}
    for tool, fields in CORRECTIONS.items():
        for key, value in fields.items():
            assert PRIOR[tool][key] != value


# ── Planted faults: each is applied to a deep copy of the live list ──


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def _drop_headroom(tools, instructions):
    return [t for t in tools if t.name != "fable_headroom"], instructions


def _blank_title(tools, instructions):
    _tool(tools, "fable_headroom").annotations.title = "   "
    return tools, instructions


def _no_annotations(tools, instructions):
    _tool(tools, "fable_headroom").annotations = None
    return tools, instructions


def _missing_readonly(tools, instructions):
    _tool(tools, "fable_headroom").annotations.readOnlyHint = None
    return tools, instructions


def _idempotent_flip(tools, instructions):
    _tool(tools, "fable_headroom").annotations.idempotentHint = False
    return tools, instructions


def _destructive_dropped(tools, instructions):
    _tool(tools, "fable_consult").annotations.destructiveHint = None
    return tools, instructions


def _title_changed(tools, instructions):
    _tool(tools, "fable_headroom").annotations.title = "Fable Eligibility"
    return tools, instructions


def _guarded_read_only(tools, instructions):
    _tool(tools, "fable_consult").annotations.readOnlyHint = True
    return tools, instructions


def _long_description(tools, instructions):
    _tool(tools, "fable_headroom").description = "x" * (MAX_DESCRIPTION + 1)
    return tools, instructions


def _param_description_deleted(tools, instructions):
    del _tool(tools, "fable_consult").inputSchema["properties"]["role"]["description"]
    return tools, instructions


def _param_description_blank(tools, instructions):
    _tool(tools, "fable_consult").inputSchema["properties"]["brief"]["description"] = "   "
    return tools, instructions


def _long_instructions(tools, instructions):
    return tools, "x" * (MAX_INSTRUCTIONS + 1)


def _long_property_name(tools, instructions):
    props = _tool(tools, "fable_consult").inputSchema["properties"]
    props["n" * 65] = props.pop("scope")
    return tools, instructions


def _bad_property_char(tools, instructions):
    props = _tool(tools, "fable_consult").inputSchema["properties"]
    props["bad name"] = props.pop("scope")
    return tools, instructions


def _relax(*dropped):
    """The pin and the correction with the named (tool, field) entries removed, so
    the sibling rule that reads the same field cannot also fire and the plant goes
    red alone. Returns the keyword arguments for ``violations``."""
    prior = {tool: dict(fields) for tool, fields in PRIOR.items()}
    corrections = {tool: dict(fields) for tool, fields in CORRECTIONS.items()}
    for tool, key in dropped:
        prior[tool].pop(key, None)
        corrections.get(tool, {}).pop(key, None)
    return {"prior": prior, "corrections": corrections}


# (id, rule it must trip, fault, prior entries to drop so no sibling rule fires)
PLANTED = [
    ("count_tool_dropped", "1_count", _drop_headroom, ()),
    ("title_blank", "2_title_and_readonly_present", _blank_title, (("fable_headroom", "title"),)),
    (
        "annotations_absent",
        "2_title_and_readonly_present",
        _no_annotations,
        (("fable_headroom", "destructiveHint"), ("fable_headroom", "idempotentHint"),
         ("fable_headroom", "openWorldHint")),
    ),
    ("readonly_missing", "2_title_and_readonly_present", _missing_readonly, ()),
    ("prior_idempotent_flipped", "3_prior_unchanged", _idempotent_flip, ()),
    ("prior_destructive_dropped", "3_prior_unchanged", _destructive_dropped, ()),
    ("prior_title_changed", "3_prior_unchanged", _title_changed, ()),
    ("guarded_declared_read_only", "4_guarded_is_write", _guarded_read_only,
     (("fable_consult", "readOnlyHint"),)),
    ("description_over_cap", "5_description_cap", _long_description, ()),
    ("param_description_deleted", "6_param_description", _param_description_deleted, ()),
    ("param_description_blank", "6_param_description", _param_description_blank, ()),
    ("instructions_over_cap", "7_instructions_cap", _long_instructions, ()),
    ("property_name_too_long", "8_property_name", _long_property_name, ()),
    ("property_name_bad_char", "8_property_name", _bad_property_char, ()),
]


def test_every_rule_has_a_planted_fault():
    assert {rule for _, rule, _, _ in PLANTED} == set(RULES)


def test_blank_title_and_blank_parameter_are_planted():
    ids = {pid for pid, _, _, _ in PLANTED}
    assert {"title_blank", "param_description_blank"} <= ids


@pytest.mark.parametrize("plant", PLANTED, ids=[p[0] for p in PLANTED])
def test_planted_fault_goes_red_alone(plant):
    _, rule, fault, dropped = plant
    tools = [t.model_copy(deep=True) for t in live_tools()]
    # Control first: the untouched copy is green, so any red below is the plant's.
    assert violations(tools, mcp.instructions) == {}
    tools, instructions = fault(tools, mcp.instructions)
    assert set(violations(tools, instructions, **_relax(*dropped))) == {rule}


def test_cap_boundary_is_inclusive():
    tools = [t.model_copy(deep=True) for t in live_tools()]
    _tool(tools, "fable_headroom").description = "x" * MAX_DESCRIPTION
    assert violations(tools, "x" * MAX_INSTRUCTIONS) == {}


def test_property_name_boundary_is_inclusive():
    tools = [t.model_copy(deep=True) for t in live_tools()]
    props = _tool(tools, "fable_consult").inputSchema["properties"]
    props["n" * 64] = props.pop("scope")
    assert violations(tools, mcp.instructions) == {}
