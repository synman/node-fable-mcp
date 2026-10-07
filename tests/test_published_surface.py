"""Published surface — docstring pitfalls, annotations, tool inventory."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))


def _load_entry():
    entry = _SERVICES / "node-fable-mcp.py"
    spec = importlib.util.spec_from_file_location("node_fable_mcp_entry", str(entry))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENTRY = _load_entry()

README = Path(__file__).resolve().parent.parent / "README.md"
CONSULT_CAP = 2048  # Claude Code cuts a tool description at this many characters


def _reference_text() -> str:
    """The README's `fable_consult reference` section: the full caller-facing
    text (13 pitfalls, every argument, the return shape) that was moved out of
    the tool description when that description had to fit the 2,048-character
    cap. A pitfall that cannot live in the description must still be pinned
    somewhere, so these tests read it here (raw `README.md`, not a rendering).
    """
    text = README.read_text(encoding="utf-8")
    start = text.index("## fable_consult reference")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def test_exactly_two_tools_registered():
    names = sorted(t.name for t in ENTRY.mcp._tool_manager.list_tools())
    assert names == ["fable_consult", "fable_headroom"]


def test_fable_consult_reference_names_all_13_pitfalls():
    """Re-homed from the docstring: the thirteen pitfalls (and the `(n)` markers
    the Args lines cite) moved verbatim into the README reference section when
    the tool description was cut to fit 2,048 characters. The description keeps
    the cost, write guard and credential rules and points at the README."""
    ref = _reference_text()
    for n in range(1, 14):
        assert f"({n})" in ref, f"pitfall marker ({n}) missing from the README reference"
    doc = ENTRY.fable_consult.__doc__
    assert "README.md" in doc, "the description must point at the README reference"
    assert len(doc) <= CONSULT_CAP


def _collapsed(doc: str) -> str:
    """Whitespace-collapsed docstring text — a marker or phrase that wraps
    across a line break is present but unfindable by a raw substring match
    against the triple-quoted source (tests/test_scrub.py's own lesson)."""
    return " ".join(doc.split())


def _any(text: str, *patterns: str) -> bool:
    """True when ANY pattern matches — a concept pinned by a synonym set.

    A docstring assertion keyed to one exact word fails on every honest
    reword, and the cheapest way to make it pass again is to weaken the
    docstring, which inverts what the test is for. This does not make the
    check semantic — no test reads meaning — it only widens the vocabulary
    a correct rewrite may use.
    """
    return any(re.search(p, text) for p in patterns)


def test_pitfall_11_names_role_defaults_and_both_effort_directions():
    doc = _collapsed(_reference_text())
    start = doc.index("(11)")
    end = doc.index("(12)")
    p11 = doc[start:end]

    # Per-role defaults named, so a caller knows what they are overriding.
    for role in ("adversarial_review", "review_diff", "plan", "diagnose"):
        assert role in p11, f"pitfall 11 must name {role}'s effort default"
    assert "implement" in p11, "pitfall 11 must name implement's effort default"
    assert "high" in p11 and "medium" in p11, (
        "pitfall 11 must name both default values (high and medium)"
    )

    # Upward direction: a measured duration figure, not just an adjective
    # like "slower" or "costs more".
    assert re.search(r"\d+s\b", p11), (
        "pitfall 11 must cite a measured duration for raising effort"
    )

    # Downward direction: the substantive risk is that a low-effort answer
    # is indistinguishable from a full-effort one — state the invisibility,
    # not just that it is cheaper/faster.
    #
    # Matched as small SYNONYM SETS, not single literals. A test keyed to one
    # exact word fails on any honest reword, which teaches the next author to
    # weaken the docstring rather than fix the test — and the first draft of
    # these assertions did exactly that, pinning "shallow" and "risk" while
    # its own commit message claimed to pin substance. A set is still
    # vocabulary matching, only wider: no test can check meaning. What it buys
    # is that a rewrite saying the same thing in different words survives.
    assert "low" in p11, "pitfall 11 must name the downward direction"
    assert _any(p11, r"shallow\w*", r"thin\w*", r"less deep", r"\blight\w*"), (
        "pitfall 11 must say a low-effort answer comes back shallower"
    )
    assert _any(p11, r"invisible", r"\bflag\w*", r"undetect\w*",
                r"indistinguishable", r"unmarked"), (
        "pitfall 11 must state that a shallower answer arrives unflagged, "
        "not merely that it is cheaper"
    )

    # The max warning must survive the rewrite — the docs' own phrasing is
    # "diminishing returns / overthinking", so either word carries it.
    assert "max" in p11 and _any(p11, r"diminishing", r"overthink\w*")


def test_effort_arg_line_states_both_directions():
    # The Args block left the docstring when each parameter's text moved into the
    # live input schema (Param Schemas packet), so this reads the `effort`
    # parameter's schema description, which is what a client actually receives.
    tool = ENTRY.mcp._tool_manager.get_tool("fable_consult")
    arg = _collapsed(tool.parameters["properties"]["effort"]["description"])
    assert arg.startswith("low/medium/high/xhigh/max")

    assert "pitfall 11" in arg
    # Both directions of the dial named in the compact Args line, not just
    # "default per role" pointing at the pitfall with no hint of the stakes.
    assert _any(arg, r"rais\w*", r"\bup\b", r"higher"), (
        "arg line must name the upward direction"
    )
    assert _any(arg, r"lower\w*", r"\bdown\b", r"reduc\w*"), (
        "arg line must name the downward direction"
    )
    # The downward mention must read as a WARNING, not a neutral note — that
    # is the whole difference between documenting the dial and documenting
    # its consequence.
    assert _any(arg, r"risk\w*", r"shallow\w*", r"unflagged", r"thin\w*"), (
        "arg line must state a downside for lowering effort, not just that "
        "lowering is possible"
    )


def test_fable_consult_docstring_fleet_convention_sections():
    doc = ENTRY.fable_consult.__doc__
    assert "WHEN TO USE" in doc
    assert "WRITE GUARD" in doc
    # One fact, one writer: parameter text lives in the input schema only, so an
    # Args block in the description would duplicate it (and spend description
    # budget). test_param_schemas.py asserts every parameter is described.
    assert "Args:" not in doc
    assert "Returns:" in doc
    assert "fable_headroom" in doc  # sibling disambiguation


def test_fable_headroom_docstring_fleet_convention_sections():
    doc = ENTRY.fable_headroom.__doc__
    assert "WHEN TO USE" in doc
    assert "fable_consult" in doc  # sibling disambiguation
    assert "Returns:" in doc


def test_fable_consult_annotations():
    tool = ENTRY.mcp._tool_manager.get_tool("fable_consult")
    ann = tool.annotations
    assert ann.title == "Fable Consult"
    assert ann.readOnlyHint is False
    assert ann.destructiveHint is True
    assert ann.idempotentHint is False
    assert ann.openWorldHint is True


def test_fable_headroom_annotations():
    tool = ENTRY.mcp._tool_manager.get_tool("fable_headroom")
    ann = tool.annotations
    assert ann.title == "Fable Headroom"
    assert ann.readOnlyHint is True
    assert ann.destructiveHint is False
    assert ann.idempotentHint is True
    # Open world: the snapshot goes through cswap's accounts_snapshot(fetch=None),
    # which makes every stale account a candidate for a usage fetch, and that fetch
    # is a GET to api.anthropic.com/api/oauth/usage (claude_swap/oauth.py). A read
    # that can reach an external service is open-world; this was False before the
    # body was read.
    assert ann.openWorldHint is True


def test_fable_headroom_description_names_the_live_credential_write():
    """The refresh the description mentions is, for the logged-in account, a locked
    rewrite of Claude Code's own credential file (cswap switcher
    _fetch_active_usage). The read-only hint stays True because that write is in
    service of the read; the description is where a caller learns it happens."""
    doc = _collapsed(ENTRY.fable_headroom.__doc__)
    assert "logged-in account" in doc
    assert "live credential file" in doc
    assert len(ENTRY.fable_headroom.__doc__) <= CONSULT_CAP


def test_server_name():
    assert ENTRY.mcp.name == "node-fable-mcp"


def test_json_payload_carries_server_build_sha():
    """_json() stamps a short git SHA on every payload — the version marker
    that lets a caller distinguish a genuinely-still-broken bug from a
    long-running server that just hasn't restarted since the fix landed
    (a process only runs the code it started with). Real checkout, no mocking:
    this repo's own HEAD is a loose ref, so the value must be a real
    12-char hex prefix, not a placeholder."""
    decoded = json.loads(ENTRY._json({"status": "ok"}))
    build = decoded["server_build"]
    if build is None and not (Path(ENTRY.__file__).resolve().parent.parent / ".git").exists():
        pytest.skip("not a git checkout, so there is no build SHA to stamp")
    assert build is not None
    assert re.fullmatch(r"[0-9a-f]{12}", build), build


def test_json_never_overwrites_an_explicit_server_build():
    decoded = json.loads(ENTRY._json({"status": "ok", "server_build": "caller-set"}))
    assert decoded["server_build"] == "caller-set"


def test_server_build_sha_survives_unreadable_checkout(monkeypatch, tmp_path):
    """De-vacuum: point SERVICES_DIR's parent at a directory with no .git at
    all and confirm the function degrades to None rather than raising -- a
    diagnostics-only field must never break every payload."""
    monkeypatch.setattr(ENTRY, "SERVICES_DIR", tmp_path / "services")
    assert ENTRY._server_build_sha() is None
