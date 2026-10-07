"""Subprocess runner for one-shot headless Fable consultations via cswap.

A consultation is:

  cswap run <account> --require-session --no-share -- -p --model <model>
    --output-format json --effort <level> --strict-mcp-config
    --mcp-config <generated mcp_grant.mcp_config_json()>
    --settings <generated mcp_grant.settings_json()>
    --max-budget-usd <cap> [--allowedTools ...] [--disallowedTools ...]
    [--add-dir <path> ...] [--system-prompt-file <tmpfile>] [-r <session_id>]

NOTE on --mcp-config/--settings (Task G): both are GENERATED, not literals.
mcp_grant.py wires a read-only grant of every fleet MCP server's tools
EXCEPT claude-channel-watcher/isaac-mcp/node-federation-mcp/node-whisper-
mcp (excluded wholesale by design) — a tool with no measured
readOnlyHint is unclassified and denied exactly like a write tool
(fail-closed). Absent a measured census, both generate to their pre-Task-G
empty defaults (mcpServers:{}) plus the always-on 4-server exclusion deny.

NOTE on --allowedTools and the grant (closing the reachability gap left by
Task G): a server present in --mcp-config and absent from --settings'
permissions.deny is still UNREACHABLE under -p unless something also
ALLOWS it — --strict-mcp-config/--mcp-config only loads the schema,
--allowedTools is what a headless run actually permits a call against.
allowed_tools() therefore appends one bare server-name entry
(``mcp__<server>``) per key in ``grant["mcpServers"]`` — sourced from
mcp_grant.build_grant()'s own output, never a hand-typed list. This is
SAFE against re-admitting a denied tool because CC's permission engine
applies deny-overrides-allow — two live results, of DIFFERENT strengths,
both real evidence for the same claim:

  (a) n=1, invocation-based (2026-09-08, matched positive/negative
  control on node-extract-mcp): a server-level allow entry made the
  server's read-only tools callable while its one bare-exact-name-denied
  tool (extract_ticket_attachments) stayed NOT_VISIBLE, identical to having
  no allow entry at all — confirmed by the harness's own
  ``permission_denials`` array on an actual call attempt, the strongest
  signal available per-tool.

  (b) breadth, across the FULL real deny set (2026-09-08, a
  live probe that spent real money and is not shipped in this repository): one live call asked the subprocess to both (i) attempt an
  actual invocation of one denied tool (mcp__ticketing-mcp__cache_clear)
  and report SUCCEEDED/DENIED, and (ii) enumerate every tool it could see
  (checked by substring match against the real, independently re-derived
  275-name denied set and 340-name granted set). Result, real cost
  total_cost_usd=0.838607: 0 of 275 denied tool-level names appeared in
  the enumeration, 340 of 340 granted read-only names appeared (the
  mandatory negative control — an empty denied-intersection from a
  broken grant showing no MCP tools at all would look identical to a
  genuine pass). The enumeration method is weaker per-name than (a) — it
  is the model's free-text self-report of what it can see, not a call
  the harness itself refused — but it is the only method that has ever
  exercised the other 274 real deny entries, including
  node-git-tools-mcp's git_push/git_commit/git_reset/git_rebase,
  ticketing-mcp's incidents_create/table_update/approvals_approve/
  batch_request, cdn-mcp's purge_urls, and certs-mcp's
  approve_certificate — none of which appeared.
  DISCLOSED, not smoothed over: the invocation attempt itself did NOT
  reach the (a)-grade signal for this tool — the model self-reported
  ``CACHE-CLEAR-RESULT: DENIED`` but the harness's own
  ``permission_denials`` array came back empty for the call. Read
  together with the 0/275 enumeration result, the likely explanation is
  that a per-tool deny inside an otherwise-allowed server removes the
  tool's definition from what the model is ever offered, rather than
  offering it and rejecting the call at invocation time — which would
  make ``permission_denials`` structurally silent for exactly this
  shape, not merely unlucky this one run. The self-report remains real,
  disclosed, corroborating evidence (consistent with the independent
  enumeration result), but this docstring does not claim it reached the
  (a)-grade signal it did not reach.

A server mcp_grant.py denies WHOLESALE (bare server name, tools_total
shortfall) never appears in grant["mcpServers"] in the first place, so it
can never earn a server-level allow entry either — but allowed_tools()
also filters any grant["mcpServers"] name whose bare form appears in
grant["deny"] as a second, defense-in-depth layer on top of that
producer-side invariant, never trusting it alone.

launched with cwd = a FRESH EMPTY scratch dir (tempfile.mkdtemp), minimal
env, its own process group (start_new_session=True), the BRIEF on stdin
(NEVER argv), and the constructed role system prompt in a scratch temp FILE
whose PATH goes on argv via --system-prompt-file.

NOTE on --system-prompt-file: undocumented in ``claude --help`` at CLI
2.1.263 but live-verified functional (empirical, this deployment). Keep it —
it is the only way to hand a large system prompt without argv exposure.

NOTE on --add-dir: it is NOT a sandbox — the launch cwd tree is fully
readable regardless of grants. That is exactly WHY cwd is a fresh empty
scratch dir: an empty cwd means "readable regardless" reads nothing.

NOTE on ~/.claude specifically (closing the gap the review notes flagged
[BLOCKING] — two prior probes measured Read escaping --add-dir at /tmp and
$HOME and generalized to ~/.claude without testing it directly, and
the design's forbidden-behavior list claims that exact path is "denied
by tool-level scope"): live-measured, not inferred. 2026-09-08,
tests/test_live_reach_dot_claude.py (total_cost_usd=0.6193764999999999): an
unarmed claude-fable-5-1 call granted ONLY a fresh empty scratch dir as
material read a sentinel planted at a top-level ~/.claude path and returned
its exact marker content verbatim, alongside a /tmp positive control in the
same reply. Item 8's "denied by tool-level scope" claim is REFUTED for this
path class, exactly as it already was for /tmp and $HOME: --add-dir governs
what a headless run may reach WITHOUT an interactive permission prompt, not
what the Read tool's own path argument may resolve to once Read itself is
in --allowedTools. This does not change the containment_not_worsened
verdict's reasoning below (a granted arbitrary-path MCP tool still adds no
NEW capability class beyond what Read already had) — it replaces the one
premise that reasoning rested on without direct evidence for this class.
The sentinel was created fresh, contained only a random marker, and was
removed and verified gone in the same test run; no pre-existing ~/.claude
file was read, moved, or modified.

NOTE on flag persistence: flags/grants re-bind from argv on EVERY resume;
nothing but conversation history persists. A resume (-r) call therefore
re-passes the FULL flag set — tool grants, deny list, budget, system-prompt
file, everything.

Kill discipline: on timeout OR any cancellation/exception the ENTIRE process
group is killed (SIGTERM, grace, then os.killpg SIGKILL) in a finally block —
an orphaned Fable call bills real dollars.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

from node_fable import accounts, call_state, config, cswap_api, mcp_grant, prompts, scrub
from node_fable.errors import FableError

# Exactly three valid model ids. claude-fable-5-0 does NOT exist (404,
# live-verified) — a caller-supplied model outside this set is REJECTED
# before any subprocess launch.
MODELS = ("claude-fable-5-1", "claude-opus-5", "claude-opus-4-8")
DEFAULT_MODEL = "claude-fable-5-1"

# The two Opus fallback tiers run DIRECT — no cswap wrapper, no account
# selector, no draw against the isolated 3-account pool (Task F). Derived
# from MODELS rather than restated as a second literal tuple, so the two
# lists cannot silently diverge if a model id is ever added or removed.
# claude-fable-5-1 (DEFAULT_MODEL) always uses the cswap-managed pool.
DIRECT_INVOKE_MODELS = tuple(m for m in MODELS if m != DEFAULT_MODEL)

# Unarmed (confirmed=False): read-only file tools only. Git reads reach the
# subprocess through the Task G grant (node-git-tools-mcp's 17 read-only
# tools, classified by readOnlyHint), never through Bash. Until 2026-09-08
# this list also carried four `Bash(git <verb>:*)` allow rules; they were
# dropped because they never worked as promised: an allow rule is a
# command-PREFIX rule, the subprocess cwd is an empty scratch dir, and the
# only shapes that could reach a repo (`cd <repo> && git ...`, `git -C`
# inside a compound command) are exactly the shapes CC's headless engine
# refuses without a prompt. Measured: 4 of 4 shell git reads denied inside
# one unarmed call, none of them by the allow list. What Bash still admits
# unarmed is CC's own built-in read-only command set (ls/cat/head/tail/
# grep/find/wc/pwd and the single-command read-only git forms) — a
# harness property this list neither grants nor promises.
UNARMED_ALLOWED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
]
# Armed (confirmed=True) adds write tools + full Bash (tests need Bash).
ARMED_EXTRA_TOOLS = ["Edit", "Write", "NotebookEdit", "Bash"]

# ALWAYS passed (armed or unarmed, every role) unless git_write_override:
# the git-write deny. This is an ARGV-PATTERN deny on the literal shell
# command Bash receives — CONFIRMED undefended for one shape (wave-3
# SIGNIFICANT finding, the review notes, verified by source read: grep for
# "python" against this list returns nothing): a python-level or
# library-level commit (`python3 -c "import subprocess; subprocess.run(
# ['git','commit',...])"`, dulwich/GitPython calling
# Repo.index.commit()/porcelain.commit() directly, or any other way to
# spell the same call) carries no `git commit`/`git push` argv shape for
# these patterns to match, and is not caught.
#
# This is NOT closable by enumerating more patterns — a prior version of
# this comment claimed "an earlier measurement proves that shape separately", which was
# false; no test anywhere exercises it, and none should be built solely on
# a pattern-matching premise: once a call is armed, Bash is otherwise
# fully granted, so a finite pattern list only ever catches the shapes its
# author thought of, never the unbounded remainder. It is a speed bump
# against the literal/naive form a cooperative model produces when merely
# asked, not a boundary against a deliberate bypass.
# tests/test_argv.py::test_git_write_deny_has_no_python_or_library_coverage_disclosed_gap
# pins the absence so a future silent "closure" claim is caught immediately.
#
# What actually bounds this risk: arming requires a citable, current-turn
# operator confirmed=True before anything reaches Bash at all; implement's
# own design intent (the design's forbidden-behavior list) already
# routes committing back to the CALLER, never Fable — WHY this deny exists;
# the deny still stops the literal form; and git_write_override remains
# the one sanctioned, operator-only path to a Fable-side commit.
GIT_WRITE_DENY_TOOLS = [
    "Bash(git commit:*)",
    "Bash(git push:*)",
    "Bash(git -C * commit:*)",
    "Bash(git -C * push:*)",
    "Bash(sh -c *git commit*)",
    "Bash(sh -c *git push*)",
]

# Paths that may NEVER be granted via --add-dir (or reached by material /
# target_repo params): the CC config tree (credentials, transcripts, vault)
# and the cswap backup tree (raw credential snapshots).
_FORBIDDEN_ROOTS = ("~/.claude", "~/.claude-swap-backup")

# SIGTERM → SIGKILL grace for process-group teardown.
_KILL_GRACE_S = 5.0


def validate_model(model: str | None) -> str:
    if model is None:
        return DEFAULT_MODEL
    if model not in MODELS:
        raise FableError(
            "invalid_model",
            f"model '{model}' is not a valid Fable-consult model — exactly "
            f"three ids exist: {', '.join(MODELS)}. (claude-fable-5-0 does "
            "not exist.)",
        )
    return model


def validate_effort(role: str, effort: str | None) -> str:
    if effort is None:
        return prompts.ROLE_EFFORT_DEFAULT[role]
    if effort not in prompts.EFFORTS:
        raise FableError(
            "invalid_effort",
            f"effort '{effort}' invalid — one of: {', '.join(prompts.EFFORTS)}",
        )
    return effort


def _forbidden_roots() -> list[str]:
    return [os.path.realpath(os.path.expanduser(r)) for r in _FORBIDDEN_ROOTS]


def validate_grant_path(path: str, kind: str) -> str:
    """Validate a material/target path; return its resolved form.

    Must exist and be readable; must NOT resolve under ~/.claude or
    ~/.claude-swap-backup (which also covers the vault at
    ~/.claude/secrets.vault). Resolution uses realpath so a symlink cannot
    smuggle a forbidden tree in.
    """
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        raise FableError(
            "invalid_material",
            f"{kind} path does not exist: {path}",
        )
    if not os.access(expanded, os.R_OK):
        raise FableError(
            "invalid_material",
            f"{kind} path is not readable: {path}",
        )
    resolved = os.path.realpath(expanded)
    _assert_not_forbidden(resolved, kind, path)
    return resolved


def _assert_not_forbidden(resolved: str, kind: str, original: str) -> None:
    """Refuse a path that IS, falls UNDER, or CONTAINS a forbidden root.

    Both directions matter: a descendant of ~/.claude leaks the tree
    directly, and an ANCESTOR (e.g. $HOME itself, or "/") leaks it by
    containment the moment it becomes an --add-dir root. The containment
    direction was a review finding — granting a parent of a forbidden root
    is the same First-Law-grade exposure as granting the root.
    """
    for root in _forbidden_roots():
        if (
            resolved == root
            or resolved.startswith(root + os.sep)
            or root.startswith(resolved + os.sep)
        ):
            raise FableError(
                "forbidden_path",
                f"{kind} path {original} resolves to {resolved}, which is, "
                f"falls under, or CONTAINS a forbidden root ({root}) — the CC "
                "config tree and cswap backups are never granted to a "
                "consultation, directly or by ancestor containment.",
            )


def add_dir_roots(material: list[str] | None, target_repo: str | None, role: str) -> list[str]:
    """--add-dir roots: material paths + target_repo (diagnose/implement).

    A file path grants its parent directory (--add-dir takes directories);
    a directory path grants itself. Deduplicated, first-seen order.
    """
    roots: list[str] = []
    entries: list[tuple[str, str]] = [(p, "material") for p in (material or [])]
    if target_repo and role in prompts.REPO_ROLES:
        entries.append((target_repo, "target_repo"))
    for raw, kind in entries:
        resolved = validate_grant_path(raw, kind)
        root = resolved if os.path.isdir(resolved) else os.path.dirname(resolved)
        # The dirname widening above is where a file-in-$HOME becomes a grant
        # of $HOME itself — re-validate the DERIVED root, not just the input.
        _assert_not_forbidden(root, f"{kind} (derived --add-dir root)", raw)
        if root not in roots:
            roots.append(root)
    return roots


def allowed_tools(
    confirmed: bool,
    granted_servers: list[str] | None = None,
    denied: list[str] | None = None,
) -> list[str]:
    """The base guard-state tool set (Read/Grep/Glob, plus write tools
    when armed) PLUS one bare ``mcp__<server>`` allow entry per granted
    MCP server — the piece that makes Task G's grant reachable, and since
    2026-09-08 the ONLY git read path an unarmed call has (see
    UNARMED_ALLOWED_TOOLS).

    ``granted_servers``/``denied`` are the caller's own already-computed
    grant (``mcp_grant.build_grant()["mcpServers"].keys()`` /
    ``["deny"]``) — never re-derived here, so this function cannot
    silently disagree with the --mcp-config/--settings payloads built
    from the same grant. A server whose bare name is ALSO present in
    ``denied`` (mcp_grant.py's own wholesale-shortfall-denial path) is
    excluded from the allow entries too — defense in depth on top of
    mcp_grant.py's existing guarantee that such a server never reaches
    ``mcpServers`` in the first place, never trusted alone (see the
    module docstring's NOTE on --allowedTools for the live-verified
    deny-overrides-allow evidence this design relies on).
    """
    base = UNARMED_ALLOWED_TOOLS + ARMED_EXTRA_TOOLS if confirmed else list(UNARMED_ALLOWED_TOOLS)
    if not granted_servers:
        return base
    denied_set = set(denied or [])
    server_allows = [
        f"mcp__{name}" for name in sorted(granted_servers)
        if f"mcp__{name}" not in denied_set
    ]
    return base + server_allows


def _claude_argv(
    *,
    model: str,
    effort: str,
    budget_usd: str,
    confirmed: bool,
    add_dirs: list[str],
    system_prompt_file: str,
    session_id: str | None = None,
    mint_session_id: str | None = None,
    git_write_override: bool = False,
) -> list[str]:
    """The claude-side flags, shared verbatim by every launch path: the
    cswap-wrapped pool path (``build_argv``) and the Opus direct-invoke path
    (``build_direct_argv``, Task F). Callers prepend whatever precedes
    ``-p`` — the cswap wrapper prefix, or nothing. Duplicating this
    construction across the two paths is exactly how they would silently
    diverge later (Task F brief) — every invariant below (clean-launch
    flags, the guard-state tool set, the git-write deny, --add-dir grants,
    the budget cap, and the system-prompt-file/resume mechanics) is a
    property of THIS assembled flag set, not of the cswap wrapper.

    --effort is ALWAYS passed explicitly, even when it equals the role
    default: an implicit default is a fact about the CLI's mood, an explicit
    flag is a fact about this call.

    --mcp-config/--settings/--allowedTools's granted-server entries are all
    GENERATED on every call (Task G) from whatever census + registration
    files ``mcp_grant`` finds on disk right now — computed ONCE per call
    (a single ``mcp_grant.build_grant()``) so all three describe the
    IDENTICAL grant, never independently-read snapshots that could
    disagree with each other.

    Both payloads are refused on a secret-pattern hit, never redacted-and-
    sent — the SAME ``scan_or_refuse`` posture the assembled prompt gets in
    ``_attempt()``, not the ``redact_secrets`` posture ``classify()`` uses
    on echoed subprocess output. The distinction that governs which posture
    applies is DIRECTION, not which side happens to carry the risk today:
    ``--mcp-config``/``--settings`` are INPUT heading to the subprocess on
    argv (visible to every process on the host via ``ps``, unlike stdin) —
    exactly like the prompt — while ``classify()``'s targets are subprocess
    OUTPUT already produced (stdout/stderr this caller never chose); refuse-
    and-hide would bury a real error there, so redact-and-send is sanctioned
    only for that direction (secret-pattern hits REFUSE the call
    outright, never redact-and-send). A hit here IS a leak in a fleet MCP
    server's OWN registration (this node's ``.mcp.json`` /
    ``~/.claude.json``, forwarded verbatim by ``mcp_grant.generate_grant()``
    with no content validation) rather than something THIS call's caller
    chose — that changes WHO must act on the refusal (the operator, fixing
    the named registration — never this call's caller by rewording a brief),
    not whether one is owed. ``scrub.scan_mcp_servers_or_refuse`` scans each
    granted server's registration individually so the refusal NAMES the
    offending server (the matched value itself stays withheld) instead of
    pointing at an opaque blob. It covers ``--mcp-config`` (the payload that
    forwards registration content — including any ``env`` block — verbatim)
    and, by construction, ``--settings`` too: that payload's ``deny`` list
    is built entirely from server/tool NAMES (this function's own
    ``excluded``/census inputs), never registration content, so it carries
    nothing this scan does not already cover by scanning the registrations
    those names are drawn from — no separate scan of the ``--settings`` text
    itself is a distinct check, only a redundant one. No current
    registration on this node embeds a literal secret (vault-only auth is
    the convention here); this refusal exists for a future
    violation of that convention, not a response to a live one.
    """
    grant = mcp_grant.build_grant()
    scrub.scan_mcp_servers_or_refuse(grant["mcpServers"])
    mcp_config = mcp_grant.mcp_config_json(grant)
    settings = mcp_grant.settings_json(grant)
    argv = [
        "-p",
        "--model", model,
        "--output-format", "json",
        "--effort", effort,
        "--strict-mcp-config",
        "--mcp-config", mcp_config,
        "--settings", settings,
        "--max-budget-usd", budget_usd,
        "--allowedTools", *allowed_tools(
            confirmed,
            granted_servers=list(grant["mcpServers"].keys()),
            denied=grant["deny"],
        ),
    ]
    if not git_write_override:
        argv += ["--disallowedTools", *GIT_WRITE_DENY_TOOLS]
    for d in add_dirs:
        argv += ["--add-dir", d]
    argv += ["--system-prompt-file", system_prompt_file]
    if session_id:
        argv += ["-r", session_id]
    elif mint_session_id:
        # Fresh call: assign OUR OWN id rather than let the CLI generate one
        # invisibly. `--session-id <uuid>` is a genuine claude CLI flag for
        # a new (non-resume) session — mutually exclusive with `-r` above,
        # never both. This is what makes a killed/timed-out consultation
        # still addressable: the id is known and call-state-recorded BEFORE
        # spawn, so a kill leaves a resumable handle instead of an orphaned
        # transcript nobody can name (an earlier finding).
        argv += ["--session-id", mint_session_id]
    return argv


def build_argv(
    *,
    account: str,
    model: str,
    effort: str,
    budget_usd: str,
    confirmed: bool,
    add_dirs: list[str],
    system_prompt_file: str,
    session_id: str | None = None,
    mint_session_id: str | None = None,
    git_write_override: bool = False,
) -> list[str]:
    """Exact cswap-wrapped consultation argv. The brief is NEVER here — it
    goes on stdin. This is the account-selector path's argv (claude-fable-5-1
    today); the Opus fallback tiers use ``build_direct_argv`` instead
    (Task F), reusing the identical claude-side flags via ``_claude_argv``.
    """
    prefix = ["cswap", "run", account, "--require-session", "--no-share", "--"]
    return prefix + _claude_argv(
        model=model,
        effort=effort,
        budget_usd=budget_usd,
        confirmed=confirmed,
        add_dirs=add_dirs,
        system_prompt_file=system_prompt_file,
        session_id=session_id,
        mint_session_id=mint_session_id,
        git_write_override=git_write_override,
    )


def build_direct_argv(
    *,
    model: str,
    effort: str,
    budget_usd: str,
    confirmed: bool,
    add_dirs: list[str],
    system_prompt_file: str,
    session_id: str | None = None,
    mint_session_id: str | None = None,
    git_write_override: bool = False,
) -> list[str]:
    """Opus fallback direct-invoke argv (Task F) — NO cswap wrapper, NO
    ``--require-session``: this path runs on whatever account is already the
    active default login in the calling session's own scope, so there is no
    account to swap into and no cswap-internal gate to pass
    (``--require-session`` only fires inside ``cswap run``'s own swap logic —
    it is never dropped from an existing call, it simply never applies
    here). Every other invariant is identical to ``build_argv`` because both
    share ``_claude_argv``.
    """
    return ["claude"] + _claude_argv(
        model=model,
        effort=effort,
        budget_usd=budget_usd,
        confirmed=confirmed,
        add_dirs=add_dirs,
        system_prompt_file=system_prompt_file,
        session_id=session_id,
        mint_session_id=mint_session_id,
        git_write_override=git_write_override,
    )


def _minimal_env() -> dict[str, str]:
    """Minimal subprocess env: cswap/claude need HOME + PATH + USER, nothing
    else of ours. Deliberately NOT a copy of os.environ — the parent MCP
    server's env can carry tokens this call must never see.

    ``USER`` is REQUIRED, not cosmetic. The claude CLI resolves its stored
    OAuth credentials through the macOS keychain, and that lookup is keyed on
    ``USER``; with the variable absent the CLI does not error on the keychain
    — it reports a well-formed result whose body is ``Not logged in · Please
    run /login`` and whose cost is 0, which reads as an account/login problem
    rather than a missing environment variable. Measured 2026-09-08 by
    bisecting the parent env against a live probe: every other candidate
    group (proxy/CA vars, XPC/session vars, ``LOGNAME``, ``SHELL``,
    ``__CF_USER_TEXT_ENCODING``) left the call unauthenticated, and ``USER``
    alone restored it. ``LOGNAME`` carries the same value and does NOT
    substitute — it was tested on its own and failed.
    """
    env = {
        "HOME": os.path.expanduser("~"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "USER": os.environ.get("USER") or os.path.basename(os.path.expanduser("~")),
        "TERM": "dumb",
    }
    for passthrough in ("TMPDIR", "LANG", "LC_ALL"):
        if os.environ.get(passthrough):
            env[passthrough] = os.environ[passthrough]
    return env


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGTERM the whole group, grace, then SIGKILL. Never raises."""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + _KILL_GRACE_S
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    # Reap the direct child after a hard kill — a long-lived MCP server
    # process would otherwise accumulate one zombie per hard-killed
    # consultation (review finding).
    try:
        proc.wait(timeout=1.0)
    except Exception:
        pass


def _spawn(argv: list[str], brief: str, cwd: str, timeout: float) -> tuple[int, str, str, bool]:
    """Launch the consultation subprocess. Returns (rc, stdout, stderr,
    timed_out). The finally block guarantees the process GROUP is dead on
    every exit path — timeout, cancellation, or any exception — because an
    orphaned Fable call keeps billing real dollars."""
    proc = None
    try:
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=_minimal_env(),
                start_new_session=True,
                text=True,
            )
        except OSError as exc:
            return 127, "", f"failed to launch cswap: {exc}", False
        try:
            stdout, stderr = proc.communicate(input=brief, timeout=timeout)
            return proc.returncode, stdout, stderr, False
        except subprocess.TimeoutExpired:
            return -1, "", f"timeout after {timeout}s", True
    finally:
        if proc is not None and proc.poll() is None:
            _kill_process_group(proc)


_RATE_LIMIT_RE = re.compile(
    r"rate[ _-]?limit|too many requests|\b429\b|usage limit|quota exceeded",
    re.IGNORECASE,
)
# Window attribution markers for a 429 body. Only an explicit 7d marker
# attributes to the weekly window; anything else stays honest about not
# knowing (never assumed 7d).
_SEVEN_DAY_RE = re.compile(r"seven[ _-]?day|\b7[ -]?d(?:ay)?\b|weekly", re.IGNORECASE)
_FIVE_HOUR_RE = re.compile(r"five[ _-]?hour|\b5[ -]?h(?:our)?\b", re.IGNORECASE)

# Refusal heuristic (conservative): a NON-error JSON result whose text is
# short and OPENS with a first-person refusal formula, with no code fence.
# Known risks (documented, accepted):
#   false positive — a terse legitimate answer that happens to open with
#     "I can't reproduce this..." is misclassified as refused;
#   false negative — a refusal phrased without a first-person opener (or
#     buried after preamble) is misclassified as ok.
# Callers are told (pitfall 9) that refused detection is heuristic.
_REFUSAL_OPENER_RE = re.compile(
    r"^\s*(i['’]m sorry|i can(?:no|['’])t\b|i cannot\b|i won['’]t\b|"
    r"i am (?:unable|not able)\b|i['’]m (?:unable|not able)\b|i must decline\b)",
    re.IGNORECASE,
)
_REFUSAL_MAX_LEN = 600


def _looks_refused(result_text: str | None) -> bool:
    if not isinstance(result_text, str):
        return False
    text = result_text.strip()
    if not text or len(text) > _REFUSAL_MAX_LEN or "```" in text:
        return False
    return bool(_REFUSAL_OPENER_RE.search(text))


def _rate_limited_payload(blob: str, account: str) -> dict:
    if _SEVEN_DAY_RE.search(blob):
        attribution = "7d"
    elif _FIVE_HOUR_RE.search(blob):
        attribution = "5h"
    else:
        attribution = "unattributable"
    # Attributable-to-7d IS reactive-failover-eligible; an UNATTRIBUTABLE
    # 429 is ALSO reactive-failover-eligible on the current account — it is
    # simply never recorded as a 7d fact.
    return {
        "status": "error",
        "failure_class": "rate_limited",
        "account": account,
        "window_attribution": attribution,
        "failover_eligible": True,
        "message": "consultation hit a rate limit on this account",
    }


def classify(
    rc: int, stdout: str, stderr: str, timed_out: bool, *, account: str,
    model: str = "", session_id: str = "",
) -> dict:
    """Classify a finished attempt into EXACTLY one of the six failure
    classes (no_headroom lives in the selector, not here) or an ok payload:
    subprocess_error, malformed_output, timeout, refused, rate_limited.

    ``model`` is the REQUESTED tier — every error payload carries it, and
    the refused message NAMES it, because the caller-driven ladder depends
    on the caller knowing which tier just refused (closing-review finding:
    a default call never states a model, so an unnamed refusal strands the
    caller guessing where on the ladder it stands).

    ``session_id`` is the id the CALLER already knows before this subprocess
    ever ran — either the resume target, or the fresh id ``_attempt`` minted
    and passed via ``--session-id`` before spawn (an earlier finding
    2026-09-08 23:19 EDT: a timeout previously returned NO session_id at
    all, because the CLI's own JSON — the only place a resume-only reading
    ever came from — never prints when the process is killed mid-run,
    leaving an unaddressable transcript on disk with no handle back to it).
    It is attached to EVERY branch below, including the three that return
    before any stdout is parsed (timeout, the non-JSON subprocess_error, and
    malformed_output) — those are exactly the branches the old
    ``data.get("session_id")`` read could never reach, since there was no
    ``data`` yet to read it from."""
    if timed_out:
        return {
            "status": "error",
            "failure_class": "timeout",
            "account": account,
            "model": model,
            "session_id": session_id or None,
            "message": stderr or "consultation timed out",
        }
    blob = f"{stdout}\n{stderr}"
    data = None
    try:
        data = json.loads(stdout) if stdout and stdout.strip() else None
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict):
        if _RATE_LIMIT_RE.search(blob):
            payload = _rate_limited_payload(blob, account)
            payload["model"] = model
            payload["session_id"] = session_id or None
            return payload
        if rc != 0:
            return {
                "status": "error",
                "failure_class": "subprocess_error",
                "account": account,
                "model": model,
                "session_id": session_id or None,
                "returncode": rc,
                # Echoed subprocess output is secret-redacted before it can
                # land in the caller's transcript (review finding) — the
                # inbound scrub covers the prompt, not what cswap/claude
                # emit on their own error paths.
                "message": scrub.redact_secrets(
                    (stderr or stdout or "no output").strip()[-2000:]
                ),
            }
        # Zero exit, non-JSON stdout: malformed. No strip-and-retry — the
        # contract is one JSON object; anything else is a defect upstream.
        return {
            "status": "error",
            "failure_class": "malformed_output",
            "account": account,
            "model": model,
            "session_id": session_id or None,
            "message": "stdout is not a JSON object",
            "stdout_prefix": scrub.redact_secrets((stdout or "")[:400]),
        }
    # The CLI's own reported id should equal what we told it to use via
    # --session-id/-r; prefer the CALLER's known id regardless, so a
    # divergence (which would itself be a CLI-behavior regression worth
    # surfacing, not silently swallowing) never produces a payload with no
    # id at all.
    session_id = session_id or data.get("session_id")
    if rc != 0 or data.get("is_error"):
        # api_error_status == 429 is the precise signal; the regex fallback
        # runs over STDERR ONLY on this branch — a JSON result whose TEXT
        # merely discusses rate limits (plausible when that is the
        # consultation's subject) must not be misclassified (review finding).
        if data.get("api_error_status") == 429 or _RATE_LIMIT_RE.search(stderr or ""):
            payload = _rate_limited_payload(blob, account)
            payload["session_id"] = session_id
            payload["model"] = model
            return payload
        return {
            "status": "error",
            "failure_class": "subprocess_error",
            "account": account,
            "model": model,
            "returncode": rc,
            "session_id": session_id,
            "terminal_reason": data.get("terminal_reason"),
            "message": scrub.redact_secrets(
                str(data.get("result") or stderr or "error result").strip()[-2000:]
            ),
        }
    result_text = data.get("result")
    if _looks_refused(result_text):
        tier = model or "(unknown tier)"
        return {
            "status": "error",
            "failure_class": "refused",
            "account": account,
            "model": model,
            "refused_tier": tier,
            "session_id": session_id,
            "message": (
                f"the {tier} tier declined the request (heuristic "
                "classification — see pitfall 9). Do not resume this "
                "session; rework the brief and retry THIS tier once fresh "
                "before escalating to the next ladder tier (pitfall 12)."
            ),
            "result": result_text,
        }
    # Force the caller's known id onto the ok payload too, so an "ok" result
    # can never disagree with every error branch above about which id this
    # attempt used — a real CLI mismatch would otherwise pass through
    # silently as data['session_id'] rather than surface as the regression
    # it would be.
    data["session_id"] = session_id
    return {"status": "ok", "data": data, "account": account}


def _model_used(data: dict) -> str | None:
    """The PRIMARY model that answered — the modelUsage entry with the
    largest costUSD, never merely the first key: an earlier measurement measured that
    real Opus-tier calls list a claude-haiku side-call (~$0.001, title
    generation) FIRST, so first-key would banner the wrong model on exactly
    the fallback calls the loud disclosure exists for (closing-review
    finding)."""
    usage = data.get("modelUsage")
    if isinstance(usage, dict) and usage:
        def _cost(v: object) -> float:
            if isinstance(v, dict):
                c = v.get("costUSD")
                if isinstance(c, (int, float)):
                    return float(c)
            return 0.0
        return max(usage.items(), key=lambda kv: _cost(kv[1]))[0]
    return None


def _fallback_disclosure(model_used: str | None) -> str | None:
    if model_used is None or model_used.startswith("claude-fable"):
        return None
    return (
        f"⚠️ FALLBACK MODEL ANSWERED: {model_used} — this is NOT Fable. "
        "Treat depth-of-reasoning claims accordingly; re-run on "
        "claude-fable-5-1 when its headroom returns if a Fable-grade answer "
        "is required."
    )


def _ok_payload(data: dict, *, role: str, effort: str, account: str) -> dict:
    model_used = _model_used(data)
    disclosure = _fallback_disclosure(model_used)
    result_text = data.get("result")
    if disclosure and isinstance(result_text, str):
        result_text = f"{disclosure}\n\n{result_text}"
    return {
        "status": "ok",
        "role": role,
        "model_used": model_used,
        "session_id": data.get("session_id"),
        "cost_usd": data.get("total_cost_usd"),
        "duration_ms": data.get("duration_ms"),
        "num_turns": data.get("num_turns"),
        "effort": effort,
        "account": account,
        "result": result_text,
        "fallback_disclosure": disclosure,
        "permission_denials": data.get("permission_denials"),
    }


def _email_slug(email: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", email)


def _active_account_email(snapshot=None) -> str:
    """Whichever account cswap reports as the live default login RIGHT NOW.

    Deliberately reads the FULL cswap snapshot rather than the
    allowlist-filtered ``accounts.account_rows()``: the Opus direct-invoke
    path (Task F) runs on whatever is already active in the calling
    session's own scope, which need not be a member of the 3-account Fable
    pool at all — there is no selector here to intersect against an
    allowlist. ``snapshot`` is injectable for tests (mirrors
    ``accounts.account_rows(snapshot=None, ...)``'s own DI pattern); the
    default is the real, live cswap read.
    """
    if snapshot is None:
        snapshot = cswap_api.accounts_snapshot()
    for acct in snapshot.accounts:
        if acct.is_active:
            return acct.email
    raise FableError(
        "no_active_account",
        "cswap reports no account as the active default login — the Opus "
        "direct-invoke path has no account to run the consultation "
        "against.",
    )


def _apply_armed_stop(payload: dict) -> None:
    """Attach the armed-Stop disclosure to an error payload of a
    confirmed=True call whose failure may have landed after writing began
    (rate_limited / timeout / subprocess_error). The refusal class is
    excluded — a refusal produced no mutation attempt. Appends rather than
    overwrites, so class-specific detail survives."""
    if payload.get("failure_class") not in {"rate_limited", "timeout", "subprocess_error"}:
        return
    payload["armed_stop"] = True
    note = (
        "ARMED Stop — not retried anywhere: the target tree may carry a "
        "partial mutation. Inspect it (git status / git diff) before any "
        "further implement attempt, armed or unarmed."
    )
    prior = payload.get("message")
    payload["message"] = f"{prior} | {note}" if prior else note


def _scratch_dir_for(session_id: str) -> str:
    """Deterministic, session-keyed scratch cwd -- the SAME absolute path on
    a fresh mint and any later resume of that same session_id.

    Claude persists a conversation incrementally (verified empirically,
    2026-09-10: real historical consult transcripts up to ~400KB exist
    under ~/.claude-swap-backup/sessions/<account>/projects/<hashed-cwd>/
    <session-id>.jsonl even for calls whose --output-format json blob was
    never printed) -- but that project directory name is derived from the
    subprocess's CWD, not just the session id. The prior behavior minted a
    brand-new random tempdir (tempfile.mkdtemp) on EVERY call, so a resume
    attempt hashed to a DIFFERENT project directory than the original call
    ever wrote to and would find nothing there regardless of whether Claude
    had genuinely persisted turns. Keying the scratch path off the session
    id (instead of a random suffix) is the fix: -r on a later call now
    launches from the identical cwd the first attempt used, so Claude's own
    lookup resolves to the transcript it already wrote.

    Recreated EMPTY on every call (the caller's finally still rmtree's it
    after each attempt) -- the security property the module docstring
    states ("an empty cwd means 'readable regardless' reads nothing") is
    about emptiness, not path unpredictability, so a stable path satisfies
    it identically to a random one.
    """
    d = config.state_dir() / "scratch" / session_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(d)


def _attempt(
    *,
    account: str,
    system_prompt: str,
    brief: str,
    model: str,
    effort: str,
    confirmed: bool,
    add_dirs: list[str],
    session_id: str | None,
    git_write_override: bool,
    role: str,
    store: call_state.CallStateStore,
    direct: bool = False,
) -> dict:
    """One subprocess attempt on one account. Scrub chokepoint runs HERE,
    immediately before the spawn, on the fully assembled prompt — every
    path (fresh, resume, any account/tier, cswap-wrapped or direct) funnels
    through this function. ``direct=True`` builds the Opus fallback
    direct-invoke argv (Task F) instead of the cswap-wrapped one; ``account``
    is still required either way — it labels the per-account lock and the
    call-state row, and the active account is NOT exempt from those
    (Task F: self-review rejection, refused-resume rejection, 429
    cooldowns all still apply to it).

    ``session_id`` here is the RESUME target (or None for a fresh call) —
    same parameter as before. On a fresh call this function mints its own
    id, records a ``pending`` call-state row for it BEFORE the subprocess
    spawns, passes it to the CLI via ``--session-id``, and hands the SAME
    id to ``classify()`` so every returned payload — including a timeout,
    which never gets to read the CLI's own JSON — carries a real
    session_id (an earlier finding). A resume call's
    own target is never re-recorded as pending here; that row already
    exists and is left alone until ``consult()``'s ``_record`` overwrites it
    with this attempt's outcome.

    A timed-out consult IS now a real resume candidate, corrected
    2026-09-10 -- see ``_scratch_dir_for``'s docstring for the mechanism
    (Claude persists incrementally; the scratch cwd is what makes the
    persisted transcript findable again) and its own caveat: this is
    unverified against a genuine live timeout as of this commit, since
    confirming it costs real account spend. Report back whether a real
    -r on a timed-out session_id actually continues from prior turns."""
    scrub.scan_or_refuse(system_prompt + "\n\n" + brief)
    mint_id = session_id or str(uuid.uuid4())
    if not session_id:
        store.record_pending(mint_id, role=role, account=account)
    lock_path = config.locks_dir() / f"{_email_slug(account)}.lock"
    scratch = _scratch_dir_for(mint_id)
    try:
        prompt_file = os.path.join(scratch, "system-prompt.md")
        with open(prompt_file, "w", encoding="utf-8") as fh:
            fh.write(system_prompt)
        argv_kwargs = dict(
            model=model,
            effort=effort,
            budget_usd=config.max_budget_usd(),
            confirmed=confirmed,
            add_dirs=add_dirs,
            system_prompt_file=prompt_file,
            session_id=session_id,
            mint_session_id=None if session_id else mint_id,
            git_write_override=git_write_override,
        )
        argv = (
            build_direct_argv(**argv_kwargs)
            if direct
            else build_argv(account=account, **argv_kwargs)
        )
        # Per-account file lock held for the subprocess duration: two server
        # processes (one per CC session) must not run concurrent
        # consultations on the same account.
        with open(lock_path, "w", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                rc, out, err, timed_out = _spawn(argv, brief, scratch, config.timeout_s())
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        return classify(
            rc, out, err, timed_out, account=account, model=model, session_id=mint_id,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def consult(
    *,
    role: str,
    brief: str,
    material: list[str] | None = None,
    confirmed: bool = False,
    session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    scope: str | None = None,
    target_repo: str | None = None,
    git_write_override: bool = False,
    alias_internal_names: bool = False,
) -> dict:
    """Orchestrate one consultation: validate, select, attempt, classify.

    Per call: at most ONE subprocess attempt per candidate account, at most
    N total (N = candidate count). Only a rate_limited failure moves to the
    next candidate; any other failure returns immediately. This cap is
    separate from the model ladder, which is CALLER-driven across calls.
    """
    if role not in prompts.ROLES:
        raise FableError(
            "invalid_role",
            f"unknown role '{role}' — valid roles: {', '.join(prompts.ROLES)}",
        )
    if not isinstance(brief, str) or not brief.strip():
        raise FableError("invalid_brief", "brief must be a non-empty string")
    resolved_model = validate_model(model)
    # Task F: the caller's model choice, not session_id, decides the launch
    # mechanism for THIS call — a resumed session pinned to an account that
    # was already active when minted must stay direct too, or wrapping it in
    # `cswap run <account> --require-session` would refuse it outright
    # (Fixed Inputs: --require-session raises for the CURRENT default-login
    # account).
    direct = resolved_model in DIRECT_INVOKE_MODELS
    resolved_effort = validate_effort(role, effort)
    add_dirs = add_dir_roots(material, target_repo, role)
    if alias_internal_names:
        brief = scrub.alias_internal_names(brief)
    system_prompt = prompts.build_prompt(
        role,
        confirmed=confirmed,
        material=material,
        scope=scope,
        target_repo=target_repo,
    )
    store = call_state.CallStateStore(config.call_state_path())

    def _record(payload: dict, account: str) -> None:
        sid = payload.get("session_id") or (payload.get("data") or {}).get("session_id")
        if sid:
            store.record_result(
                sid,
                role=role,
                account=account,
                status=payload.get("status", "error"),
                terminal_reason=(payload.get("data") or {}).get("terminal_reason")
                if payload.get("status") == "ok"
                else payload.get("terminal_reason"),
                failure_class=None if payload.get("status") == "ok" else payload.get("failure_class"),
            )

    if session_id:
        # RESUME: never runs the account selector — targets the account
        # recorded for this session_id (rule d; sessions are account-pinned).
        row = call_state.validate_resume(store, session_id, role)
        account = row["account"]
        # A fable-5-1 session can now be PINNED TO THE ACTIVE ACCOUNT, since
        # `is_active` stopped being a selector exclusion (accounts.py module
        # docstring). Such a session was minted through the direct argv, and
        # wrapping its resume in `cswap run <account> --require-session`
        # would be refused outright for the same reason the mint was — cswap
        # cannot swap into the login it is already on. So the launch path is
        # decided by BOTH the model tier and the live active account, never
        # the model alone; deciding it on the model alone is what would have
        # broken every resume of an active-account fable session.
        resume_direct = direct or account == _active_account_email()
        if direct:
            # The cswap-wrapped path enforces the pin STRUCTURALLY: every
            # launch is prefixed `cswap run <account> --require-session`,
            # and --require-session REFUSES to launch at all when `account`
            # is already the active default login (claude_swap.session
            # SessionManager.run(), ~line 553 — it raises rather than
            # falling back to a plain launch) instead of silently running
            # unwrapped. So a cswap-wrapped mint's transcript is ALWAYS
            # written under that account's own isolated session profile
            # (session_dir_for(): <backup>/sessions/<num>-<slug>/projects/
            # ...), never under the shared ~/.claude/projects/ a
            # direct-invoke launch reads/writes. The direct-invoke path
            # (Task F's Opus tiers) has no such isolation: build_direct_argv()
            # never sets CLAUDE_CONFIG_DIR, so a direct-invoke mint AND a
            # direct-invoke resume both always hit the ONE shared
            # ~/.claude/projects/ store, keyed on whatever account happens
            # to be the active default login at the time — a session minted
            # this way is pinned to whichever account was active AT MINT
            # (see the fresh-mint branch below, `account =
            # _active_account_email()`), and that pin can drift from the
            # NOW-active account by resume time (e.g. an intervening
            # `cswap use`). Verified this does NOT fail loudly on that
            # drift: the transcript file sits at the same physical path
            # regardless of which account's credentials are currently
            # active, so an unmatched-active resume would silently continue
            # the pinned session under the CURRENT account's
            # credentials/quota instead of refusing. (A session minted on
            # the cswap-wrapped path and later resumed direct cannot hit
            # this: its transcript lives under the OTHER account's isolated
            # profile dir, so the shared store has nothing at that
            # session_id and the resume fails loudly instead — "No
            # conversation found...", exit 1, no spawn cost, verified
            # empirically.) Refuse BEFORE any spawn rather than let the
            # shared, unswapped transcript store paper over the mismatch.
            active = _active_account_email()
            if active != account:
                raise FableError(
                    "account_mismatch",
                    f"session '{session_id}' is pinned to account "
                    f"'{account}', but the active default login is now "
                    f"'{active}'. The Opus direct-invoke path runs against "
                    "whichever account is currently active, with no "
                    "account-swap mechanism of its own, so it cannot honor "
                    "this session's recorded pin. Refusing before spawn "
                    "(fail closed) rather than risk resuming under the "
                    "wrong account's credentials. Switch the active login "
                    f"back to '{account}' and retry, or start a fresh "
                    "session instead.",
                )
        payload = _attempt(
            account=account,
            system_prompt=system_prompt,
            brief=brief,
            model=resolved_model,
            effort=resolved_effort,
            confirmed=confirmed,
            add_dirs=add_dirs,
            session_id=session_id,
            git_write_override=git_write_override,
            role=role,
            store=store,
            direct=resume_direct,
        )
        if payload.get("failure_class") == "rate_limited":
            store.set_cooldown(account, config.cooldown_s())
        _record(payload, account)
        if payload["status"] == "ok":
            return _ok_payload(payload["data"], role=role, effort=resolved_effort, account=account)
        if confirmed:
            # Disclosure symmetry (closing-review finding): arming by
            # resuming the unarmed session is the PRIMARY armed path, and
            # a 429/timeout/crash here may equally land after writing began.
            # The resume path never retries anyway (account-pinned, no
            # selector) — this attaches the same Stop disclosure.
            _apply_armed_stop(payload)
        return payload

    if direct:
        # Opus fallback tiers (Task F): skip the cswap account selector
        # entirely — no accounts.account_rows()/require_candidates() call,
        # no draw against the isolated 3-account pool. Run ONE attempt
        # against whatever account is already the active default login in
        # the calling session's own scope. Every other invariant still
        # applies (the Opus direct-invoke path follows the same
        # rules) — the shared call-state store still locks/records/cools
        # down this account exactly like a selector-path account; it is NOT
        # exempt from self-review rejection, refused-resume rejection, or
        # 429 cooldowns.
        account = _active_account_email()
        payload = _attempt(
            account=account,
            system_prompt=system_prompt,
            brief=brief,
            model=resolved_model,
            effort=resolved_effort,
            confirmed=confirmed,
            add_dirs=add_dirs,
            session_id=None,
            git_write_override=git_write_override,
            role=role,
            store=store,
            direct=True,
        )
        if payload.get("failure_class") == "rate_limited":
            store.set_cooldown(account, config.cooldown_s())
        _record(payload, account)
        if payload["status"] == "ok":
            return _ok_payload(payload["data"], role=role, effort=resolved_effort, account=account)
        if confirmed:
            # Same armed-Stop disclosure as the selector path — a 429/crash/
            # timeout here may equally land after writing began, and there
            # is no other account to fail over to anyway (there was never a
            # candidate list on this path).
            _apply_armed_stop(payload)
        return payload

    # FRESH: selector path (claude-fable-5-1 — the cswap-managed pool).
    rows = accounts.account_rows(cooldowns=store.cooldowns())
    candidates = accounts.require_candidates(rows)
    attempts: list[dict] = []
    for cand in candidates:
        account = cand["email"]
        # The ACTIVE account is a candidate like any other (the operator's
        # rule is enabled + 5h avail > 0 + 7d avail > 0 + greatest Fable
        # avail — `is_active` is not one of its terms). What `is_active`
        # decides is only HOW to launch it: `cswap run --require-session`
        # REFUSES the current default login and raises before anything
        # starts, so the active account runs through the direct, no-wrapper
        # argv Task F already built. Same selector, same ranking, same
        # call-state locking/recording/cooldowns — only the launch mechanism
        # differs, and it differs because cswap cannot swap into the account
        # it is already on.
        payload = _attempt(
            account=account,
            system_prompt=system_prompt,
            brief=brief,
            model=resolved_model,
            effort=resolved_effort,
            confirmed=confirmed,
            add_dirs=add_dirs,
            session_id=None,
            git_write_override=git_write_override,
            role=role,
            store=store,
            direct=bool(cand.get("is_active")),
        )
        if payload.get("failure_class") == "rate_limited":
            store.set_cooldown(account, config.cooldown_s())
            _record(payload, account)
            if confirmed:
                # ARMED Stop (never auto-retried): a 429/crash/timeout during
                # an armed call may land AFTER it began writing, and the
                # server cannot know. Failing over to another account against
                # a possibly-dirty tree is exactly the forbidden retry — the
                # caller must inspect the tree (git status/diff) first.
                _apply_armed_stop(payload)
                return payload
            # UNARMED only: rate_limited moves to the next candidate.
            attempts.append(payload)
            continue
        _record(payload, account)
        if payload["status"] == "ok":
            return _ok_payload(payload["data"], role=role, effort=resolved_effort, account=account)
        if confirmed:
            _apply_armed_stop(payload)
        payload["attempts_before_this"] = attempts
        return payload
    return {
        "status": "error",
        "failure_class": "rate_limited",
        "message": (
            f"all {len(attempts)} candidate account(s) rate-limited; each is "
            "now in cooldown — retry later or check fable_headroom."
        ),
        "attempts": attempts,
    }
