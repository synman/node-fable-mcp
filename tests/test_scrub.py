"""Scrub — secret refusal (patterns imported from the commit gate), jargon
neutralization in built prompts, aliasing, forbidden --add-dir roots."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parent.parent / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from node_fable import prompts, runner, scrub
from node_fable.errors import FableError

# A planted AWS-access-key-shaped string (synthetic — the canonical
# example-shaped value, not a real credential).
PLANTED_AWS = "AKIA" + "IOSFODNN7EXAMPLE"


def test_default_patterns_are_the_built_in_list(monkeypatch):
    monkeypatch.delenv("NODE_FABLE_SECRET_PATTERNS_PATH", raising=False)
    monkeypatch.setattr(scrub, "_patterns", None)
    from node_fable import secret_patterns

    assert scrub.secret_patterns() is secret_patterns.SECRET_PATTERNS
    assert "AWS access key id" in [label for _p, label in scrub.secret_patterns()]


def test_override_file_replaces_the_built_in_list(monkeypatch, tmp_path):
    f = tmp_path / "mine.py"
    f.write_text('import re\nSECRET_PATTERNS = [(re.compile(r"ZZTOP[0-9]{4}"), "my pattern")]\n')
    monkeypatch.setenv("NODE_FABLE_SECRET_PATTERNS_PATH", str(f))
    monkeypatch.setattr(scrub, "_patterns", None)
    assert [label for _p, label in scrub.secret_patterns()] == ["my pattern"]
    with pytest.raises(FableError) as ei:
        scrub.scan_or_refuse("see ZZTOP1234 here")
    assert ei.value.payload()["pattern_class"] == "my pattern"
    scrub.scan_or_refuse(f"clean text {PLANTED_AWS[:10]}")  # the built-in list is gone


@pytest.mark.parametrize("body", ["SECRET_PATTERNS = []\n", "OTHER = 1\n", "raise RuntimeError('boom')\n"])
def test_override_file_that_gives_no_patterns_fails_closed(monkeypatch, tmp_path, body):
    f = tmp_path / "bad.py"
    f.write_text(body)
    monkeypatch.setenv("NODE_FABLE_SECRET_PATTERNS_PATH", str(f))
    monkeypatch.setattr(scrub, "_patterns", None)
    with pytest.raises(FableError) as ei:
        scrub.scan_or_refuse("anything")
    assert ei.value.error_class == "missing_dependency"


def test_override_path_that_does_not_exist_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("NODE_FABLE_SECRET_PATTERNS_PATH", str(tmp_path / "absent.py"))
    monkeypatch.setattr(scrub, "_patterns", None)
    with pytest.raises(FableError) as ei:
        scrub.scan_or_refuse("anything")
    assert ei.value.error_class == "missing_dependency"


def test_aws_key_positive_control_refuses_and_withholds_value():
    with pytest.raises(FableError) as ei:
        scrub.scan_or_refuse(f"please review this config: {PLANTED_AWS}")
    err = ei.value
    assert err.error_class == "secret_refused"
    payload = err.payload()
    # names the pattern CLASS, withholds the value everywhere
    assert payload["pattern_class"] == "AWS access key id"
    assert PLANTED_AWS not in str(payload)
    assert PLANTED_AWS not in str(err)


def test_clean_text_passes():
    assert scrub.scan_or_refuse("an ordinary brief about a python bug") is None


def test_private_key_block_refused():
    # Assembled from parts so the literal marker never sits in this source
    # file — the commit gate scans added lines and would (correctly) refuse
    # the commit otherwise, same reason PLANTED_AWS is concatenated.
    marker = "-----BEGIN RSA " + "PRIVATE KEY-----"
    with pytest.raises(FableError):
        scrub.scan_or_refuse(marker + "\nabc")


# ── jargon neutralization (builder text only) ────────────────────────────────

def test_jargon_terms_list_committed():
    assert "isaac" in scrub.JARGON_TERMS
    assert "SynBot" not in scrub.JARGON_TERMS


def test_neutralize_jargon_replaces_terms():
    out = scrub.neutralize_jargon("the isaac tracker and the Three Laws")
    assert "isaac" not in out.lower()
    assert "three laws" not in out.lower()
    assert "tracker" not in out.lower()


def test_built_prompt_carries_no_jargon():
    for role in prompts.ROLES:
        text = prompts.build_prompt(
            role, confirmed=False, material=None,
            scope="isaac tracker sweep" if role == "plan" else None,
            target_repo=None,
        )
        lowered = text.lower()
        for term in scrub.JARGON_TERMS:
            assert term.lower() not in lowered, (role, term)
        # Task I: the tool-use fragment is a THIRD builder piece present in
        # every role's prompt (role-agnostic doctrine) and it survives the
        # same jargon check above — extending this test per-role rather than
        # adding a parallel loop, since the fragment's own jargon-absence is
        # otherwise indistinguishable from the template's.
        assert "granted external tool" in text, (role, "tool-use fragment missing")


def test_built_prompt_guard_state_and_right_sizing():
    unarmed = prompts.build_prompt("plan", confirmed=False, material=None,
                                   scope=None, target_repo=None)
    armed = prompts.build_prompt("implement", confirmed=True, material=None,
                                 scope=None, target_repo="/tmp")
    assert "UNARMED" in unarmed
    assert "ARMED" in armed
    for text in (unarmed, armed):
        assert "cheapest adequate model tier" in text


def test_unarmed_fragment_names_the_granted_git_read_tool_and_survives_scrub():
    """2026-09-08: the unarmed fragment stopped promising 'read-only git
    commands' (no shell git path exists unarmed) and now points at the
    node-git-tools-mcp grant. The tool name passes through
    neutralize_jargon with the rest of the builder text — a neutralized name
    is a nonexistent tool — so pin that it survives byte-for-byte, and pin
    that the retired promise is gone."""
    unarmed = prompts.build_prompt("plan", confirmed=False, material=None,
                                   scope=None, target_repo=None)
    assert "mcp__node-git-tools-mcp__git_status" in unarmed
    assert "no shell git path" in unarmed
    assert "read-only git commands" not in unarmed


def test_prompts_are_distinct_per_role():
    texts = {
        role: prompts.build_prompt(role, confirmed=False, material=None,
                                   scope=None, target_repo=None)
        for role in prompts.ROLES
    }
    assert len(set(texts.values())) == len(prompts.ROLES)


# ── Task I — tool-use fundamentals fragment ──────────────────────────────────

def test_tool_use_fragment_actually_routes_through_scrub_chokepoint(monkeypatch):
    """Proves the fragment PASSES THROUGH neutralize_jargon (requirement 2 of
    the Verification ceiling), not merely that its committed text happens to
    contain no jargon. Monkeypatch the fragment to a string CARRYING jargon
    and confirm build_prompt's output does not — if this test passed with
    the patch reverted to a no-op, it would prove nothing about routing, so
    the mutation IS the test: a fragment that bypassed the chokepoint (e.g.
    appended after scrub.neutralize_jargon instead of before) would leave
    the planted term in the output and this assertion would fail."""
    planted = "isaac tracker replaces this whole test-use fragment for a probe"
    monkeypatch.setattr(prompts, "_TOOL_USE_FRAGMENT", planted)
    text = prompts.build_prompt(
        "plan", confirmed=False, material=None, scope=None, target_repo=None,
    )
    assert "isaac" not in text.lower()
    assert "tracker" not in text.lower()
    # and the neutral replacement text landed, proving the swap ran rather
    # than the whole fragment being silently dropped
    assert "the internal framework" in text.lower()


def test_tool_use_fragment_measured_token_ceiling():
    """Requirement 4: an EXPLICIT, MEASURED ceiling — never assumed. The
    committed fragment is 480 chars / 80 words (measured via len()/split()
    at authoring time, recorded in the module comment); this test asserts
    the live string against both the module's own declared ceiling constant
    and a floor proving the ceiling isn't vacuously large. A regression that
    balloons the fragment toward template-sized prose fails this before it
    ever reaches a live call."""
    frag = prompts._TOOL_USE_FRAGMENT
    chars = len(frag)
    words = len(frag.split())
    assert chars <= prompts.TOOL_USE_FRAGMENT_MAX_CHARS, (
        f"tool-use fragment grew to {chars} chars, ceiling is "
        f"{prompts.TOOL_USE_FRAGMENT_MAX_CHARS}"
    )
    # floor: the ceiling constant must not be so loose it accepts anything —
    # pin it within 50% headroom of the measured value, not an arbitrary big
    # number.
    assert prompts.TOOL_USE_FRAGMENT_MAX_CHARS <= chars * 2, (
        "ceiling constant is not actually measured against this fragment"
    )
    assert 40 <= words <= 150, (words, "fragment should be short doctrine, not prose")


def test_tool_use_fragment_describes_g_actual_mechanism():
    """Requirement 5: coupled to Task G's REAL generated output
    (mcp_grant.generate_grant), not a hypothetical read-only surface.
    Cross-checks the fragment's claims against mcp_grant's own documented
    mechanism rather than restating it independently."""
    from node_fable import mcp_grant

    frag = prompts._TOOL_USE_FRAGMENT.lower()
    # G's mechanism: a bare deny name REMOVES the tool from context entirely
    # (mcp_grant.py docstring: "removes that tool from the subprocess's own
    # context entirely (genuinely undiscoverable)") — never merely a
    # call-time block. The fragment must say "removed", not just "blocked".
    assert "removed" in frag
    assert "not merely blocked" in frag
    # G fails closed on no readOnlyHint (unclassified == denied, same as
    # write) — the fragment must describe every visible mcp__ tool as
    # read-only, not merely "mostly" or "generally".
    assert "read-only" in frag
    assert "no declared read-only status" in frag
    # the naming convention the fragment tells Fable to recognize matches
    # the shape mcp_grant.generate_grant actually denies by
    # (mcp__<server>__<tool>), not a made-up prefix.
    assert "mcp__" in frag
    # sanity: mcp_grant really does fail closed on unclassified tools (the
    # exact behavior this fragment describes) — read the real function
    # rather than trusting the docstring alone.
    grant = mcp_grant.generate_grant(
        {"servers_reached": [{
            "name": "probe-server", "read_only": 1,
            "write_tool_names": [], "unclassified_tool_names": ["mystery_tool"],
        }], "servers_failed": []},
        {"probe-server": {"command": "true"}},
    )
    assert "mcp__probe-server__mystery_tool" in grant["deny"]


# ── aliasing (optional, off by default) ──────────────────────────────────────

def test_alias_internal_names_deterministic():
    text = "host 10.0.0.24 and vm-01.corp; again 10.0.0.24"
    out = scrub.alias_internal_names(text)
    assert "10.0.0.24" not in out
    assert out.count("host-ip-1") == 2
    assert "internal-host-1" in out


# ── forbidden --add-dir roots ────────────────────────────────────────────────

def test_add_dir_refuses_dot_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    with pytest.raises(FableError) as ei:
        runner.validate_grant_path(str(claude_dir), "material")
    assert ei.value.error_class == "forbidden_path"


def test_add_dir_refuses_claude_swap_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    backup = tmp_path / ".claude-swap-backup"
    backup.mkdir()
    with pytest.raises(FableError) as ei:
        runner.validate_grant_path(str(backup), "material")
    assert ei.value.error_class == "forbidden_path"


def test_add_dir_refuses_path_nested_under_forbidden_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    nested = tmp_path / ".claude" / "projects"
    nested.mkdir(parents=True)
    with pytest.raises(FableError) as ei:
        runner.validate_grant_path(str(nested), "material")
    assert ei.value.error_class == "forbidden_path"


def test_add_dir_refuses_symlink_into_forbidden_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    secret_dir = tmp_path / ".claude"
    secret_dir.mkdir()
    link = tmp_path / "innocent"
    os.symlink(secret_dir, link)
    with pytest.raises(FableError) as ei:
        runner.validate_grant_path(str(link), "material")
    assert ei.value.error_class == "forbidden_path"


def test_add_dir_missing_path_is_invalid_material(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(FableError) as ei:
        runner.validate_grant_path(str(tmp_path / "nope"), "material")
    assert ei.value.error_class == "invalid_material"


def test_add_dir_file_grants_parent_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "repo" / "notes.md"
    f.parent.mkdir()
    f.write_text("hello", encoding="utf-8")
    roots = runner.add_dir_roots([str(f)], None, "plan")
    assert roots == [os.path.realpath(str(f.parent))]

# ── ancestor containment + verbatim paths + echoed-output redaction ─────────
# (review findings)

def test_add_dir_refuses_ancestor_of_forbidden_root(tmp_path, monkeypatch):
    """Granting a DIRECTORY that CONTAINS ~/.claude (e.g. $HOME itself) is
    the same exposure as granting ~/.claude — refused in the inverse
    direction too."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    with pytest.raises(FableError) as ei:
        runner.validate_grant_path(str(tmp_path), "material")
    assert ei.value.error_class == "forbidden_path"


def test_add_dir_refuses_file_directly_in_home(tmp_path, monkeypatch):
    """A FILE directly under $HOME derives $HOME as its --add-dir root via
    the dirname widening — the DERIVED root must be re-validated, not just
    the input path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    f = tmp_path / "notes.md"
    f.write_text("hello", encoding="utf-8")
    with pytest.raises(FableError) as ei:
        runner.add_dir_roots([str(f)], None, "plan")
    assert ei.value.error_class == "forbidden_path"


def test_built_prompt_keeps_material_paths_verbatim():
    """Builder text is neutralized; material/target_repo path listings are
    appended AFTER neutralization, verbatim — a neutralized path is a
    nonexistent path Fable could never open."""
    jargon_path = "/Users/x/ws/whisper-tool/services"
    text = prompts.build_prompt(
        "diagnose", confirmed=False, material=[jargon_path],
        scope=None, target_repo="/Users/x/ai/isaac-demo",
    )
    assert jargon_path in text
    assert "/Users/x/ai/isaac-demo" in text
    # and the template/fragment half is still neutralized
    body_before_paths = text.split("Target repository", 1)[0]
    assert "isaac" not in body_before_paths.lower()


def test_redact_secrets_substitutes_pattern_class():
    """Echoed subprocess output is redact-not-refuse: matches are replaced
    with their pattern class; surrounding error text survives."""
    out = scrub.redact_secrets(f"auth failed for key {PLANTED_AWS} on retry")
    assert PLANTED_AWS not in out
    assert "[redacted:AWS access key id]" in out
    assert "auth failed" in out
    # clean text passes through untouched
    assert scrub.redact_secrets("no secrets here") == "no secrets here"


# ── Insulation chokepoint test suite ─────────────────────────────
# One positive control PER secret-pattern class the gate exposes (fail-closed
# on each), the prose-vs-diff diagnostic-quote case measured honestly, and a
# scrubbed-brief-still-works control. Every planted value is synthetic and
# assembled by concatenation so the commit gate never sees a literal marker
# in this file's own added lines.

_CLASS_CONTROLS = {
    "AWS access key id": "AKIA" + "IOSFODNN7EXAMPLE",
    "GitHub token": "ghp_" + "a" * 24,
    "secret API key (sk-...)": "sk-" + "b" * 24,
    "private key block": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "hardcoded credential literal": "password = " + '"hunter2hunter2"',
}


def test_positive_control_per_secret_pattern_class():
    """Non-vacuous per-class coverage: every class the gate exposes has a
    control here that REFUSES, and this test fails if the gate grows a class
    we have no control for (the scan surface must never silently widen past
    the tested set)."""
    from node_fable.errors import FableError as FE
    labels = {label for _p, label in scrub.secret_patterns()}
    assert labels == set(_CLASS_CONTROLS), (
        "gate classes changed — add/remove a positive control to match"
    )
    for label, planted in _CLASS_CONTROLS.items():
        with pytest.raises(FE) as ei:
            scrub.scan_or_refuse(f"context before {planted} context after")
        assert ei.value.error_class == "secret_refused"
        assert ei.value.payload()["pattern_class"] == label
        assert planted not in str(ei.value.payload())


def test_scrubbed_brief_still_authenticates():
    """A brief that TALKS about credentials without carrying one passes —
    the chokepoint refuses values, not vocabulary."""
    brief = (
        "The login flow rejects the password env var when it is unset; "
        "diagnose why the api key rotation job skips accounts whose "
        "auth token has expired."
    )
    assert scrub.scan_or_refuse(brief) is None


def test_diagnostic_quote_of_buggy_credential_line_is_refused_by_design():
    """The prose-vs-diff case, measured honestly: the imported patterns are
    tuned for added diff lines, so a diagnose brief QUOTING a buggy line
    like password = "..." IS refused even though the value may be fake.
    That is the documented, accepted posture (fail closed, never guess
    which quoted credentials are real) — the caller redacts the value from
    the quote and retries, per the refusal's own message. This test pins
    the behavior so a future 'helpful' relaxation is a deliberate act."""
    from node_fable.errors import FableError as FE
    quoted = 'the bug is here: password = ' + '"hunter2hunter2"  # line 40'
    with pytest.raises(FE) as ei:
        scrub.scan_or_refuse(f"Diagnose this snippet: {quoted}")
    assert ei.value.error_class == "secret_refused"
    # and the same shape with the VALUE redacted by the caller passes —
    # NOTE the placeholder must be short (<8 chars) or contain a space:
    # the credential pattern matches any >=8-char unspaced quoted value, so
    # a quoted <redacted>-style token after the equals sign would STILL
    # refuse (measured, pinned here) — the comment avoids writing that
    # shape literally so the commit gate does not refuse THIS file.
    still_refused = 'password = ' + '"<redacted>"'
    with pytest.raises(FE):
        scrub.scan_or_refuse(f"Diagnose: {still_refused}")
    redacted = 'the bug is here: password = "***"  # line 40'
    assert scrub.scan_or_refuse(f"Diagnose this snippet: {redacted}") is None


# Every role states its own write boundary in its OWN template — the boundary
# is a RULE that maps to the role, never a tool the role is denied. Operator,
# 2026-09-08 (uuid 9660a73b): implement implementing "is is job and we should
# not cage it"; and (uuid ea43ec82) plan must still keep plan files, designs,
# docs, KB topics and KB trackers current. So the armed tool set stays uniform
# across roles by design, and this test pins the thing that actually differs.
#
# implement's marker is assembled rather than written literally: the verbs it
# names are guarded tokens, and a PreToolUse guard scans a whole bash command
# for them — writing the phrase inline would deny any shell call carrying this
# file's text. Same reason the neighbouring test splits its credential shape.
_GIT_WRITE_VERBS = "git " + "commit or " + "git " + "push"

# Markers are matched against WHITESPACE-COLLAPSED prompt text: every template
# hard-wraps at ~76 chars, so a boundary sentence spanning a line break is
# present and unfindable by a naive substring match. Measured, not assumed —
# the first draft of this test reported adversarial_review as having NO
# boundary line when its template carries one, split across two lines.
ROLE_BOUNDARY_MARKERS = {
    "adversarial_review": ["you are the reviewer, not the author"],
    "review_diff": ["you are the reviewer, not the author"],
    "plan": [
        "you are the planner, not the author",
        # plan's carve-out: its deliverable IS a document, so writing one is
        # the job. Losing this line would silently re-cage the role.
        "are all yours to create and keep current",
    ],
    "diagnose": ["do not apply fixes unless write access has been explicitly enabled"],
    "implement": ["never run " + _GIT_WRITE_VERBS],
}


def _collapsed(role):
    text = prompts.build_prompt(
        role, confirmed=False, material=None,
        scope="fine" if role == "plan" else None,
        target_repo=None,
    )
    return " ".join(text.lower().split())


def test_every_role_template_states_its_own_write_boundary():
    assert set(ROLE_BOUNDARY_MARKERS) == set(prompts.ROLES), (
        "a role gained or lost a template without a boundary marker"
    )
    for role, markers in ROLE_BOUNDARY_MARKERS.items():
        text = _collapsed(role)
        for marker in markers:
            assert marker in text, (role, marker)


def test_plan_boundary_permits_documentation_upkeep():
    """plan may not touch implementation source, but its own artifacts are
    the job. Asserted separately from the marker sweep because the PAIR is
    the contract: a prohibition without the carve-out is the cage the
    operator refused, and a carve-out without the prohibition is no boundary
    at all.

    Note what the wording may NOT say: the operator's phrasing named "kb
    trackers", and `tracker` is a JARGON_TERM. The carve-out is therefore
    expressed in Fable-facing words that mean the same thing."""
    text = _collapsed("plan")
    assert "do not modify implementation source" in text
    for artifact in ("plan files", "design notes", "documentation",
                     "knowledge-base entries", "running status document"):
        assert artifact in text, artifact


def test_role_templates_are_jargon_free_at_SOURCE_not_only_after_scrub():
    """The RAW template files carry no jargon term.

    Deliberately asserted on the template on disk, never on the built prompt.
    An assertion that the BUILT prompt is jargon-free cannot fail from a
    template edit — `neutralize_jargon` always cleans it — so it tests the
    scrub, not the wording. Measured: planting `tracker` in plan.md yields a
    built prompt reading "running work log", with zero jargon hits.

    The real hazard is that silent rewrite. A boundary line written with an
    internal term is not blocked; it is REPHRASED, so the sentence Fable
    receives is one nobody authored or reviewed — and for a line whose whole
    job is to bound a role, a quiet paraphrase is exactly the wrong failure.
    Catch it where it can still be read: at source."""
    tpl_dir = Path(prompts.__file__).resolve().parent / "prompts"
    seen = 0
    for role in prompts.ROLES:
        raw = (tpl_dir / f"{role}.md").read_text(encoding="utf-8").lower()
        seen += 1
        for term in scrub.JARGON_TERMS:
            assert term.lower() not in raw, (role, term)
    assert seen == len(prompts.ROLES) == 5, seen


def test_extra_internal_tlds_are_aliased_only_when_configured(monkeypatch):
    text = "reach build-01.acme and build-01.corp"
    monkeypatch.delenv("NODE_FABLE_EXTRA_INTERNAL_TLDS", raising=False)
    out = scrub.alias_internal_names(text)
    assert "build-01.acme" in out and "build-01.corp" not in out
    monkeypatch.setenv("NODE_FABLE_EXTRA_INTERNAL_TLDS", "acme, .Staging")
    out = scrub.alias_internal_names(text + " and db.staging")
    assert "build-01.acme" not in out and "db.staging" not in out
