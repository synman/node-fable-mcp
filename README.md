# node-fable-mcp

One-shot headless Fable consultations via the `cswap` account switcher — a
FastMCP stdio server exposing exactly two tools:

| Tool | What it does | Costs money |
|---|---|---|
| `fable_consult` | Launch (or resume) a role-framed headless consultation — adversarial_review, review_diff, plan, diagnose, implement | **Yes** (canary: $0.557 for a two-word brief) |
| `fable_headroom` | Read-only per-account Fable eligibility (utilization windows, 5h/7d gate, Fable-headroom rank, cooldowns, selector pick) | No |

Most consultations run as `cswap run <account> --require-session --no-share
-- -p ...`; a consultation on the account that is the **current default
login** drops the wrapper and runs `claude -p ...` directly, because cswap
cannot swap into the login it is already on. Either way the launch is a
fresh empty scratch cwd, minimal env, its own process group, with the brief
on **stdin** (never argv) and the constructed role system prompt handed via
`--system-prompt-file`. Secrets in the assembled prompt refuse the call
outright (patterns kept in one module, `services/node_fable/secret_patterns.py` —
never redact-and-send).

Account selection is four terms and nothing else: not `disabled`, 5h
available > 0, 7d available > 0, and among those the greatest remaining
Fable availability. Being the active login is **not** a term — it selects
the launch path above, never eligibility.

## Prerequisites

- **cswap** ([claude-swap](https://github.com/realiti4/claude-swap)) cloned or
  installed with its source checkout readable — point `NODE_FABLE_CSWAP_SRC` at
  its `src/` directory (default `~/ai/claude-swap/src`, wherever you cloned it). The
  managed accounts you intend to use must already be added to cswap.
- **claude CLI** on `PATH` (cswap launches it; `--system-prompt-file`
  verified functional at CLI 2.1.263).
- **`NODE_FABLE_ACCOUNTS`** — comma-separated candidate account emails.
  Required; the server fails closed without it (portable code ships no
  personal emails).
- **Python 3.12+** with the `mcp` package (for example in a virtualenv).
- Optional: your own secret-pattern list. The built-in list is
  `services/node_fable/secret_patterns.py`; `NODE_FABLE_SECRET_PATTERNS_PATH` points at a
  Python file that defines its own `SECRET_PATTERNS` and replaces it.

## Setup

```bash
cd node-fable-mcp
python3 -m pytest tests/ -q     # subprocess and cswap are mocked — no spend
```

No install step: the entry point is `services/node-fable-mcp.py`, which
bootstraps its own `sys.path` (the `services/node_fable` package sits beside it).

### Environment (NODE_FABLE_* prefix)

| Var | Default | Meaning |
|---|---|---|
| `NODE_FABLE_ACCOUNTS` | *(required)* | Comma-separated candidate account emails |
| `NODE_FABLE_CSWAP_SRC` | `~/ai/claude-swap/src` | claude-swap source dir (imported read-only, in-process) |
| `NODE_FABLE_SECRET_PATTERNS_PATH` | *(unset: built-in list)* | Python file defining `SECRET_PATTERNS` that replaces the built-in list |
| `NODE_FABLE_EXTRA_INTERNAL_TLDS` | *(unset)* | Comma-separated extra top-level domains the optional hostname aliasing treats as internal |
| `NODE_FABLE_MCP_CENSUS_PATH` | `~/.node-fable-mcp/mcp-tool-census.json` | Persisted read-only tool census used to build the per-launch MCP grant |
| `NODE_FABLE_MCP_JSON_PATH` | `~/.mcp.json` | MCP registry read when building the grant |
| `NODE_FABLE_CLAUDE_JSON_PATH` | `~/.claude.json` | Claude Code config read when building the grant |
| `NODE_FABLE_WORKSPACE_PATHS_PATH` | `~/.node-fable-mcp/workspace-paths` | Optional `key=path` file the census uses to locate server roots |
| `NODE_FABLE_MAX_BUDGET_USD` | `50.00` | `--max-budget-usd` cap per consultation (default) |
| `NODE_FABLE_TIMEOUT_S` | `3600` | Subprocess timeout; on expiry the whole process group is killed (a `plan` at high effort died at an earlier 900s default with all work lost) |
| `NODE_FABLE_COOLDOWN_S` | `3600` | How long a rate-limited account is held out of the selector |
| `NODE_FABLE_STATE_DIR` | `~/.node-fable-mcp` | Shared call-state + per-account locks |

## Usage

### fable_headroom (spends no model money — check first)

```
fable_headroom()
→ {"status":"ok",
   "accounts":[{"email":"...","is_active":false,
                "windows":[{"label":"5h","pct_used":12.0,"resets_at":"..."},
                           {"label":"7d","pct_used":48.0,"resets_at":"..."},
                           {"label":"Fable","pct_used":30.0,"resets_at":null}],
                "five_h_avail":88.0,"seven_d_avail":52.0,
                "fable_headroom":70.0,"gate_ok":true,
                "usage_age_s":42.1,"in_cooldown":false}, ...],
   "selector_would_pick":"..."}
```

Percentages are **utilization** (% used); headroom ("available") = 100 −
window pct. Eligibility is a GATE, not a single figure — an account must
show `five_h_avail > 0` **and** `seven_d_avail > 0` to be a candidate at
all; among gate-passing accounts, the selector ranks by `fable_headroom`
alone (the per-model Fable window's own headroom, not the minimum of it and
5h). A window cswap never reported for an account (missing, not merely
exhausted) FAILS the 5h/7d gate — "not measured" is never voted as "passed"
— while a missing Fable reading only affects rank (that account still
qualifies, ranked last, disclosed via `fable_headroom: null`).
`usage_age_s` is the staleness of cswap's usage snapshot. The call is a read, but not a
pure local one: for a stale account cswap can fetch fresh usage from the account usage API
and refresh tokens (for the logged-in account the refresh runs under Claude Code's own lock
and can rewrite its live credential file), and the tool creates and prunes the local
call-state file.

### fable_consult

```
# Unarmed review (read-only tool grants):
fable_consult(role="adversarial_review",
              brief="Review the locking design in call_state.py for races.",
              material=["/path/to/repo/services/node_fable"])

# Plan with granularity (there is no elaborate/orchestrate role — both are
# plan + scope):
fable_consult(role="plan", scope="per-file edits",
              brief="Plan the migration described in the material.",
              material=["/path/to/design.md"])

# Diagnose, then hand the artifact to an armed implement:
fable_consult(role="diagnose", target_repo="/path/to/repo",
              brief="Root-cause the flaky test_x.")
# → session_id "abc123"
fable_consult(role="implement", session_id="abc123", confirmed=True,
              target_repo="/path/to/repo",
              brief="Apply the fix you diagnosed; run the tests.")
```

Read the [fable_consult reference](#fable_consult-reference) before first use — it names all 13
caller-facing pitfalls (secret refusal, arming semantics, the git-write
deny, session/resume rules, the caller-driven model-fallback ladder, cost,
and the plaintext-transcript exposure of granted material paths). The tool
description keeps the cost, write guard and credential rules and points here.

## fable_consult reference

The full caller-facing text of `fable_consult`: the thirteen numbered pitfalls, every
argument and the return shape. It lived in the tool's docstring until the tool
description had to fit the 2,048-character cap that Claude Code applies, so it is kept
here verbatim. The tool description carries the cost, the write guard, the credential
rules and a short argument list.

```text
Run a one-shot headless Fable consultation under a selected account.

WHEN TO USE: you need Fable-grade reasoning on a bounded artifact — an
adversarial review, a diff review, a plan, a root-cause diagnosis, or a
scoped implementation — and the material can be granted as local read
paths. NOT for interactive back-and-forth (each call is one headless
turn; resume exists only for the two sanctioned handoffs in pitfall 4),
and NOT for anything whose brief or material carries credentials.

Sibling: `fable_headroom` — the free read-only eligibility check; call it
first when unsure whether any account has capacity. This tool is the only
one that spends money.

WRITE GUARD: confirmed=False (default) launches UNARMED — read-only
tools (Read/Grep/Glob plus the granted read-only MCP tools; git reads go
through node-git-tools-mcp's granted read tools, there is NO shell git
path) and no Edit/Write. confirmed=True
ARMS the consultation (Edit/Write/NotebookEdit/Bash) — a real, immediate
mutation surface on the granted directories. Set confirmed=True only on
explicit operator approval you can cite. Git commit/push stay denied in
BOTH states (see pitfall 3); git_write_override=True drops that deny for
this ONE call and requires a citable current-turn operator instruction —
the server cannot verify that instruction exists; this docstring carries
the bar.

PITFALLS (all thirteen, numbered — read before first use):
  (1) A secret-pattern hit in the assembled brief/system prompt REFUSES
      the whole call, never redacts. The scan covers the assembled
      prompt only — NOT what Fable's tools read post-spawn from granted
      paths.
  (2) confirmed=True on implement is a real immediate mutation with
      tests run, not a preview — call unarmed first and read the plan.
  (3) implement never commits/pushes THE LITERAL SHELL FORM regardless
      of confirmed (scoped git-write argv-pattern deny on every
      launch); git_write_override is operator-only, per-turn. Disclosed
      gap, not closed: a python-level or library-level commit
      (dulwich/GitPython called directly) carries no argv shape for
      this deny to match and is NOT caught — do not treat "never
      commits/pushes" as an absolute guarantee against a call already
      holding armed Bash access, only as the default posture. The real
      bound is that arming already required YOUR OWN citable operator
      confirmed=True, and implement's design intent is that you review
      and commit the returned diff yourself.
  (4) Only pass session_id back for arming an unarmed implement,
      handing a diagnose artifact to implement, or resuming
      adversarial_review's OWN prior adversarial_review session —
      review_diff given a session id is ALWAYS rejected as self-review
      (it never resumes), and adversarial_review given a session id
      from any other role is rejected as self-review too. For
      adversarial_review, resume ONLY when BOTH hold: material changes
      were made since that session last ran, AND Fable will benefit
      from the prior context in its analysis and decision-making —
      otherwise start fresh. This is a caller-judgment criterion, not a
      code-level gate; the server only enforces the same-role
      provenance check above.
  (5) A refusal against a resumed session means stop resuming it —
      reframe and go fresh (the server also refuses such resumes, as
      backstop).
  (6) There is no elaborate/orchestrate role — both live under `plan`
      with the `scope` param setting granularity.
  (7) session_id is account-pinned — resume targets its minted account
      only; if that account is exhausted (or the session expired) you
      use the fresh-with-artifact fallback, never a cross-account
      resume. The fallback procedure: issue a FRESH call handed the
      exact diff plus the SHA it was authored against as material, and
      have it re-verify the tree is still at that SHA before applying.
      On an Opus-tier resume this pin is checked explicitly against the
      live active account and refused (account_mismatch) if the active
      login has changed since the session was minted — that check runs
      BEFORE any spawn, so a mismatch costs nothing.
  (8) Every call costs real dollars regardless of brief size (canary:
      $0.557 for two words) — do not spam trivial calls.
  (9) Six distinct failure classes come back: no_headroom /
      subprocess_error / malformed_output / timeout / refused /
      rate_limited — branch on each; refused detection is heuristic,
      and rate_limited attribution on error paths is ALSO heuristic
      (only an explicit api_error_status=429 is precise; window
      attribution comes from text markers and may be unattributable).
  (10) material paths must exist, be readable, and fall inside the
      granted --add-dir roots; ~/.claude and ~/.claude-swap-backup are
      never grantable — directly, or via any ancestor directory that
      contains them (granting $HOME is refused).
  (11) Per-role default: high (adversarial_review, review_diff, plan,
      diagnose) or medium (implement) — pass effort explicitly to
      override it. Raising it costs real time: high measured ~108s,
      xhigh runs 240s+ (pitfall 8's per-call dollar cost applies at
      every effort level, not just high ones). Lowering it is the
      riskier direction because it hides itself: a low-effort call
      returns the identical JSON shape and reads as a confident,
      well-formed answer, with nothing in the payload flagging that a
      review, plan, or diagnosis ran shallower than a default-effort
      call would have — for roles whose whole value is depth, that
      makes a downgrade invisible at the point of use. max sees
      diminishing returns and may run slower still.
  (12) The model-fallback ladder (claude-fable-5-1 → claude-opus-5 →
      claude-opus-4-8) is CALLER-driven across separate calls — a
      refused response NAMES the tier that refused (refused_tier +
      message); rework your brief, retry that SAME tier once fresh, and
      only then re-invoke with model=<next tier>; the server holds no
      ladder state and cannot stop you skipping tiers.
  (13) Every material path you grant is read by Fable and persists in a
      PLAINTEXT transcript under whichever account ran the call,
      including personal consumer accounts — never grant a path holding
      credentials/.env content.

Args:
    role: One of adversarial_review, review_diff, plan, diagnose,
        implement. Sets the system prompt and the effort default; no
        role is structurally barred from confirmed=True.
    brief: The task text, delivered to the subprocess on stdin (never
        argv). Refused outright on secret-pattern hits; otherwise never
        rewritten (except optional aliasing, below).
    material: Local paths to grant read access to (--add-dir roots; a
        file grants its parent directory). Validated per pitfall 10.
    confirmed: False = unarmed read-only; True = armed writes (see WRITE
        GUARD).
    session_id: Resume a prior Fable-minted session (pitfalls 4/5/7).
        Unknown ids are refused fail-closed. Omit it to mint a FRESH
        session — the server now assigns and records that id BEFORE
        spawning, so it comes back in the result even on a timeout or
        any other failure (see Returns), not only on success.
    model: One of claude-fable-5-1 (default) / claude-opus-5 /
        claude-opus-4-8; anything else is rejected before launch.
    effort: low/medium/high/xhigh/max; default per role — raising it
        costs real time, lowering it risks an unflagged shallower
        answer (pitfall 11).
    scope: plan role only — granularity of the plan (free text, e.g.
        "phases" or "per-file edits"); default "standard".
    target_repo: diagnose/implement — the repository path to grant.
    git_write_override: Drops the git-write deny for this one call.
        Operator-authorized use only (see WRITE GUARD).
    alias_internal_names: When True, applies simple hostname/IP aliasing
        to the brief before sending. Default False.

Returns:
    JSON. On success: {status:"ok", role, model_used, session_id,
    cost_usd, duration_ms, num_turns, effort, account, result,
    fallback_disclosure, permission_denials} — fallback_disclosure is
    null for claude-fable-5-1 and a loud banner (also prepended to
    result) when an opus tier answered. On failure: {status:"error",
    failure_class | error_class, session_id, message, ...} per pitfall
    9 — session_id is populated even on a timeout, since it is minted
    and call-state-recorded before the subprocess spawns rather than
    read back from the CLI's own (possibly never-printed) JSON.

    A timed-out session_id IS now a genuine resume candidate (corrected
    2026-09-10 -- an earlier version of this note claimed the opposite,
    wrongly). Claude persists a conversation incrementally regardless
    of whether --output-format json's final blob ever printed; what
    broke resume before was that every call launched from a brand-new
    random scratch cwd, so a resume's lookup hashed to a directory the
    original attempt never wrote to. The scratch cwd is now keyed by
    session_id instead (node_fable.runner._scratch_dir_for), so -r on
    a timed-out id launches from the SAME cwd the timed-out attempt
    used. Unverified against a real live timeout as of this commit --
    confirming costs real account spend -- so treat a resume of a
    timed-out session as likely-to-work, not proven, until reported
    back from an actual paid call.
```

## Project structure

```
node-fable-mcp/
├── README.md
├── LICENSE
├── evals/                       # question set + verifier for the published surface
├── services/
│   ├── node-fable-mcp.py        # FastMCP stdio entry — the two tools
│   └── node_fable/
│       ├── __init__.py
│       ├── accounts.py          # allowlist ∩ live, exclusions, headroom rank
│       ├── call_state.py        # shared fcntl-locked store: sessions, cooldowns
│       ├── config.py            # NODE_FABLE_* env resolution (fail closed)
│       ├── cswap_api.py         # read-only in-process cswap imports
│       ├── errors.py            # FableError structured errors
│       ├── secret_patterns.py   # built-in secret patterns (replaceable by env)
│       ├── prompts.py           # role system-prompt builder
│       ├── prompts/*.md         # the five role templates
│       ├── runner.py            # argv, spawn, process-group kill, classify
│       └── scrub.py             # secret refusal chokepoint + jargon list
└── tests/                       # per-file sys.path bootstrap; no conftest
```

## Registration

Add to `.mcp.json` (or the client's MCP registry). A Fable consult at
`effort=high` measured ~108s and `xhigh` can run 240s+ — long enough that
what happens to a slow tool call depends entirely on the CALLING CLIENT,
not on this server:

```json
{
  "mcpServers": {
    "node-fable-mcp": {
      "type": "stdio",
      "command": "/path/to/your/python3",
      "args": ["/path/to/node-fable-mcp/services/node-fable-mcp.py"],
      "env": {
        "NODE_FABLE_ACCOUNTS": "acct1@example.com,acct2@example.com,acct3@example.com"
      }
    }
  }
}
```

**Claude Code (measured 2.1.266):** a tool call still running after ~120s
is moved to a background task automatically — the CLI reports this inline
("moved to the background as task `<id>`") and delivers the result later as
a task-notification when the subprocess actually finishes. There is no
`MCP_TOOL_TIMEOUT` env var in this client's own process tree at all; the
120s figure is an internal ceiling, not a configurable ceiling this server
can raise or lower. **The one real hazard: the result is lost if the
calling session exits (or is killed) before the notification arrives** —
the background task does not survive the session, only the subprocess
being consulted does. Keep the session alive until the notification lands,
or spend the wait some other way and check back.

**Other MCP clients may not auto-background a slow call at all**, and will
simply time the call out and report a client-side failure while the
subprocess keeps running and billing. For those, `MCP_TOOL_TIMEOUT`
(milliseconds) is read from the CLIENT's own launch environment, NOT from
the server env block above — putting it in the server's `env` does
nothing. Launch such a client with it:

```bash
MCP_TOOL_TIMEOUT=3700000 claude
```

Either way, `NODE_FABLE_TIMEOUT_S` (default 3600s) is what actually kills
the subprocess's process group — the server, never the client, owns that
kill. A client ceiling below it just means the CLIENT gives up first while
the consultation keeps running unattended.

Use any Python 3.12+ interpreter with `mcp` installed as the command.

## Tests

```bash
cd node-fable-mcp
python3 -m pytest tests/ -q
```

All unit tests mock subprocess + cswap — the suite never launches a real
consultation and spends nothing. `tests/test_stdio_smoke.py` spawns the real
server over stdio (raw JSON-RPC) and asserts the published tool surface;
listing tools invokes nothing.

`tests/test_mcp_conformity.py` is the conformity test for the mcp-builder skill's
section 3.3, items 1 to 8, run on the live `list_tools()` and `mcp.instructions`:
tool count (2), title and `readOnlyHint` present, annotations unchanged from the
pin `c29d5719d839` (one named correction: `fable_headroom` `openWorldHint`),
guarded tools declare a write, descriptions and instructions at most 2,048
characters, every parameter described, property names valid. Each rule has planted
faults that must turn exactly that rule red.

## Contributing

Run the suite before sending a change: `python3 -m pytest tests/ -q`. Live
tests that spend real money are gated behind `NODE_FABLE_LIVE=1` and skipped
by default. Keep deployment-specific values out of the code and configurable
through the `NODE_FABLE_*` variables above.

## License

MIT. See `LICENSE`.
