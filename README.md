# node-fable-mcp

One-shot headless Fable consultations through a configured command and an
optional account switcher — a FastMCP stdio server exposing exactly two tools:

| Tool | What it does | Costs money |
|---|---|---|
| `fable_consult` | Launch (or resume) a role-framed headless consultation — adversarial_review, review_diff, plan, diagnose, implement | **Yes** (canary: $0.557 for a two-word brief) |
| `fable_headroom` | Read-only per-account Fable eligibility (utilization windows, 5h/7d gate, Fable-headroom rank, cooldowns, selector pick) | No |

Nothing about the machine is written in this repository. The command that runs a
consultation, the account switcher that wraps it, the argument templates, the
model ids, the tool names granted and denied, and every directory it reads are
**settings** (see the settings table below). A node that configures none of them
refuses a consultation with a clear "not configured" error before anything
launches, so it spends nothing.

Most consultations run through the switcher launch template
(`switcher_command` + `command_args`); a consultation on the account that is
the **current default login** drops the switcher and runs `command` +
`command_args` directly, because a switcher cannot swap into the login it is
already on. Either way the launch is a fresh empty scratch cwd, minimal env (only
what the `child_env_*` settings name), its own process group, with the brief on
**stdin** (never argv) and the constructed role system prompt handed as a file
through `system_prompt_args`. Secrets in the assembled prompt refuse the call
outright (patterns kept in one module, `services/node_fable/secret_patterns.py` —
never redact-and-send).

Account selection is four terms and nothing else: not `disabled`, 5h
available > 0, 7d available > 0, and among those the greatest remaining
Fable availability. Being the active login is **not** a term — it selects
the launch path above, never eligibility.

## Prerequisites

- **Python 3.12+** with the `mcp` package (for example in a virtualenv).
- **A consultation command** that takes the arguments your templates build and
  reads the brief on stdin. Set `command` (direct launch) and, when accounts are
  switched, `switcher_command` (a template with an `{account}` token).
- **An accounts command** that prints the accounts snapshot as JSON (format below)
  — set `accounts_command`.
- **`accounts`** — comma-separated candidate account emails. Required; the
  server fails closed without it (portable code ships no personal emails).
- Optional: your own secret-pattern list. The built-in list is
  `services/node_fable/secret_patterns.py`; `secret_patterns_path` points at a
  JSON file (a list of `{"pattern": <regex>, "label": <class>}`) that replaces it.

## Setup

```bash
cd node-fable-mcp
python3 -m pytest tests/ -q     # the command and switcher are faked — no spend
```

No install step: the entry point is `services/node-fable-mcp.py`, which
bootstraps its own `sys.path` (the `services/node_fable` package sits beside it).
Then write the settings.

### Settings

A setting is read from, in order: the environment variable `NODE_FABLE_<KEY>` (the
key in upper case), the JSON config file, a neutral default. The config file is
`$NODE_FABLE_CONFIG`, else `$XDG_CONFIG_HOME/node-fable-mcp/config.json`, else
`~/.config/node-fable-mcp/config.json`. It is one flat JSON object of string
values; a missing file is empty and an unreadable or malformed one is an error.
A list or map setting is a JSON string inside that file. A blank value counts as
unset.

In a launch template (a JSON argv list) an element may contain `{name}` tokens that
the launch fills in; an element that is exactly a list token (`{tools}`) becomes
one element per value. Tokens by setting: `switcher_command` takes `{account}`;
`command_args` takes `{model}`, `{effort}`, `{mcp_config}`, `{settings}`,
`{budget_usd}`; `allowed_tools_args` and `disallowed_tools_args` take `{tools}`;
`add_dir_args` takes `{dir}`; `system_prompt_args` takes `{file}`; `resume_args` and
`new_session_args` take `{session_id}`. A token the launch does not supply is an
error, never an empty string. The two payload templates are JSON objects:
`mcp_config_template` carries the string `"{servers}"` where the granted servers go,
`settings_template` carries `"{deny}"` where the generated deny list goes.

A safety list (`forbidden_roots`, `git_write_deny_tools`, `excluded_servers`,
`denied_tools`) must be stated: unset is refused as "not configured", and an
explicit `[]` is the operator's deliberate choice of none.

| Key | Env var | Neutral default | Controls |
|---|---|---|---|
| `config` | `NODE_FABLE_CONFIG` | `~/.config/node-fable-mcp/config.json` | Path of the config file (environment only) |
| `accounts` | `NODE_FABLE_ACCOUNTS` | none (required) | Comma-separated candidate account emails |
| `accounts_command` | `NODE_FABLE_ACCOUNTS_COMMAND` | none | JSON argv list of the command that prints the accounts snapshot |
| `accounts_timeout_s` | `NODE_FABLE_ACCOUNTS_TIMEOUT_S` | `60` | Time limit of the accounts command |
| `models` | `NODE_FABLE_MODELS` | none | JSON list of the model ids a consult may name, in fallback order |
| `default_model` | `NODE_FABLE_DEFAULT_MODEL` | first listed model | Model used when the caller names none; it launches through the switcher, the others launch directly |
| `primary_model_prefix` | `NODE_FABLE_PRIMARY_MODEL_PREFIX` | the default model | A reply from a model whose id starts with this carries no fallback banner |
| `command` | `NODE_FABLE_COMMAND` | none | JSON argv prefix that runs the consultation command directly |
| `switcher_command` | `NODE_FABLE_SWITCHER_COMMAND` | none | JSON argv template that runs it under a selected account |
| `command_args` | `NODE_FABLE_COMMAND_ARGS` | none | Argument template of every launch |
| `allowed_tools_args` | `NODE_FABLE_ALLOWED_TOOLS_ARGS` | none | Argument group that passes the allowed tool names |
| `disallowed_tools_args` | `NODE_FABLE_DISALLOWED_TOOLS_ARGS` | none | Argument group that passes the denied tool names |
| `add_dir_args` | `NODE_FABLE_ADD_DIR_ARGS` | none | Argument group that grants one directory (once per granted directory) |
| `system_prompt_args` | `NODE_FABLE_SYSTEM_PROMPT_ARGS` | none | Argument group that passes the system-prompt file path |
| `resume_args` | `NODE_FABLE_RESUME_ARGS` | none | Argument group that resumes a session |
| `new_session_args` | `NODE_FABLE_NEW_SESSION_ARGS` | none | Argument group that assigns a new session id |
| `allowed_tools` | `NODE_FABLE_ALLOWED_TOOLS` | none (no tool) | JSON list of the tool names an unarmed call may use |
| `git_read_tool_prefix` | `NODE_FABLE_GIT_READ_TOOL_PREFIX` | none | Text placed before `git_status` in the unarmed role prompt, so it names the granted git read tool by qualified name |
| `armed_tools` | `NODE_FABLE_ARMED_TOOLS` | none | JSON list of the tool names `confirmed=True` adds |
| `git_write_deny_tools` | `NODE_FABLE_GIT_WRITE_DENY_TOOLS` | none (required unless the caller overrides) | JSON list of tool patterns that deny git commit and push on every launch |
| `mcp_config_template` | `NODE_FABLE_MCP_CONFIG_TEMPLATE` | none | JSON object template of the MCP-config payload |
| `settings_template` | `NODE_FABLE_SETTINGS_TEMPLATE` | none | JSON object template of the settings payload |
| `excluded_servers` | `NODE_FABLE_EXCLUDED_SERVERS` | none (required) | JSON list of MCP servers never granted |
| `denied_tools` | `NODE_FABLE_DENIED_TOOLS` | none (required) | JSON list of qualified tool names never granted, whatever the census says |
| `forbidden_roots` | `NODE_FABLE_FORBIDDEN_ROOTS` | none (required when a path is granted) | JSON list of paths never grantable, directly or by an ancestor |
| `jargon_map` | `NODE_FABLE_JARGON_MAP` | none | JSON object, term to replacement, applied to the built role prompt |
| `mcp_registry_paths` | `NODE_FABLE_MCP_REGISTRY_PATHS` | none | JSON list of files holding `mcpServers` registrations, a later file wins |
| `mcp_census_path` | `NODE_FABLE_MCP_CENSUS_PATH` | `~/.node-fable-mcp/mcp-tool-census.json` | Persisted read-only tool census used to build the per-launch grant |
| `workspace_paths_path` | `NODE_FABLE_WORKSPACE_PATHS_PATH` | `~/.node-fable-mcp/workspace-paths` | Optional `key=path` file the census uses to locate server source |
| `child_env_passthrough` | `NODE_FABLE_CHILD_ENV_PASSTHROUGH` | none (empty environment) | JSON list of environment variable names copied to a launched child |
| `child_env_user_var` | `NODE_FABLE_CHILD_ENV_USER_VAR` | none | Name of the variable carrying the account's user name; set from this process or the home directory's base name |
| `child_env_set` | `NODE_FABLE_CHILD_ENV_SET` | none | JSON object of fixed variables set for a launched child |
| `secret_patterns_path` | `NODE_FABLE_SECRET_PATTERNS_PATH` | built-in list | JSON file that replaces the built-in secret patterns |
| `extra_internal_tlds` | `NODE_FABLE_EXTRA_INTERNAL_TLDS` | none | Comma-separated extra top-level domains the optional hostname aliasing treats as internal |
| `max_budget_usd` | `NODE_FABLE_MAX_BUDGET_USD` | `50.00` | Spend cap per consultation, passed through `{budget_usd}` |
| `timeout_s` | `NODE_FABLE_TIMEOUT_S` | `3600` | Subprocess timeout; on expiry the whole process group is killed (a `plan` at high effort died at an earlier 900s default with all work lost) |
| `cooldown_s` | `NODE_FABLE_COOLDOWN_S` | `3600` | How long a rate-limited account is held out of the selector |
| `state_dir` | `NODE_FABLE_STATE_DIR` | `~/.node-fable-mcp` | Shared call-state, per-account locks and scratch directories |

### Accounts snapshot

`accounts_command` prints one JSON object on stdout:

```json
{"accounts": [
  {"email": "alpha@example.com", "number": "1", "is_active": false, "disabled": false,
   "usage_age_s": 12.0,
   "windows": [{"label": "5h", "pct_used": 10.0, "resets_at": "..."},
               {"label": "7d", "pct_used": 20.0, "resets_at": "..."},
               {"label": "Fable", "pct_used": 30.0, "resets_at": null}]}]}
```

`pct_used` is utilization (percent used). `windows` lists only the windows the
command measured; an account with no reading carries an empty list. Anything else
on stdout, a non-zero exit, a timeout, or a command that cannot start is a
structured `missing_dependency` error. The command is read-only by contract: this
server never asks it to switch, add or remove an account.

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
5h). A window the accounts command never reported for an account (missing, not merely
exhausted) FAILS the 5h/7d gate — "not measured" is never voted as "passed"
— while a missing Fable reading only affects rank (that account still
qualifies, ranked last, disclosed via `fable_headroom: null`).
`usage_age_s` is the staleness of the usage snapshot. The call is a read, but not a
pure local one: the accounts command may fetch fresh usage for a stale account from
the account usage API and refresh tokens (and so rewrite a live credential file),
and the tool creates and prunes the local call-state file.

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
description had to fit the 2,048-character cap that MCP clients apply, so it is kept
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
tools (the `allowed_tools` setting plus the granted read-only MCP tools; git
reads go through a git tools server's granted read tools, there is NO shell
git path) and no write tools. confirmed=True
ARMS the consultation (the `armed_tools` setting) — a real, immediate
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
      On a direct-launch resume this pin is checked explicitly against the
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
      granted directory roots; the paths in the `forbidden_roots` setting
      are never grantable — directly, or via any ancestor directory that
      contains them (granting $HOME is refused when a forbidden root lies
      under it).
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
  (12) The model-fallback ladder (the `models` setting, in order) is CALLER-driven across separate calls — a
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
    material: Local paths to grant read access to (directory-grant roots; a
        file grants its parent directory). Validated per pitfall 10.
    confirmed: False = unarmed read-only; True = armed writes (see WRITE
        GUARD).
    session_id: Resume a prior Fable-minted session (pitfalls 4/5/7).
        Unknown ids are refused fail-closed. Omit it to mint a FRESH
        session — the server now assigns and records that id BEFORE
        spawning, so it comes back in the result even on a timeout or
        any other failure (see Returns), not only on success.
    model: One of the configured model ids (the default model when
        omitted); anything else is rejected before launch.
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
    null for the primary tier and a loud banner (also prepended to
    result) when a fallback tier answered. On failure: {status:"error",
    failure_class | error_class, session_id, message, ...} per pitfall
    9 — session_id is populated even on a timeout, since it is minted
    and call-state-recorded before the subprocess spawns rather than
    read back from the command's own (possibly never-printed) JSON.

    A timed-out session_id IS now a genuine resume candidate (corrected
    2026-09-10 -- an earlier version of this note claimed the opposite,
    wrongly). The command persists a conversation incrementally regardless
    of whether its final JSON blob ever printed; what
    broke resume before was that every call launched from a brand-new
    random scratch cwd, so a resume's lookup hashed to a directory the
    original attempt never wrote to. The scratch cwd is now keyed by
    session_id instead (node_fable.runner._scratch_dir_for), so a resume of
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
│   ├── node-fable-census.py     # CLI that measures the per-server tool census
│   └── node_fable/
│       ├── __init__.py
│       ├── settings.py          # environment, config file, neutral default (leaf module)
│       ├── config.py            # typed settings, state paths (fail closed)
│       ├── account_snapshot.py  # the accounts snapshot, read from a configured command
│       ├── accounts.py          # allowlist ∩ live, exclusions, headroom rank
│       ├── call_state.py        # shared fcntl-locked store: sessions, cooldowns
│       ├── census.py            # measures the read-only tool census
│       ├── mcp_grant.py         # builds the per-launch read-only MCP grant
│       ├── errors.py            # FableError structured errors
│       ├── secret_patterns.py   # built-in secret patterns (replaceable by setting)
│       ├── prompts.py           # role system-prompt builder
│       ├── prompts/*.md         # the five role templates
│       ├── runner.py            # argv, spawn, process-group kill, classify
│       └── scrub.py             # secret refusal chokepoint + jargon map
└── tests/                       # conftest.py clears every setting and the config file per test
```

## Registration

Add to the client's MCP registry. A Fable consult at
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
        "NODE_FABLE_CONFIG": "/path/to/your/node-fable-config.json"
      }
    }
  }
}
```

Put the settings in that config file (or in the `env` block); nothing in the
repository supplies them.

Some MCP clients move a tool call that is still running after about two minutes
to a background task and deliver the result later. **The one real hazard: the
result is lost if the calling session exits (or is killed) before the
notification arrives** — the background task does not survive the session, only
the subprocess being consulted does. Keep the session alive until the notification
lands, or spend the wait some other way and check back.

**Other MCP clients may not background a slow call at all**, and will
simply time the call out and report a client-side failure while the
subprocess keeps running and billing. For those, raise the client's own tool
timeout (set in the CLIENT's launch environment or configuration, NOT in the server
env block above — putting it there does nothing) above `timeout_s`.

Either way, `timeout_s` (default 3600s) is what actually kills
the subprocess's process group — the server, never the client, owns that
kill. A client ceiling below it just means the CLIENT gives up first while
the consultation keeps running unattended.

Use any Python 3.12+ interpreter with `mcp` installed as the command.

## Tests

```bash
cd node-fable-mcp
python3 -m pytest tests/ -q
```

All unit tests fake the command and the switcher — the suite never launches a real
consultation and spends nothing. `tests/conftest.py` clears every `NODE_FABLE_*`
variable and points the config file at a nonexistent path for each test, so the
machine's real configuration is never read; the `fake_cli` fixture supplies neutral
fake settings. `tests/test_settings.py` pins the setting lookup order,
`tests/test_not_configured.py` pins that an unconfigured node refuses and spends
nothing, and `tests/test_standalone.py` pins that no tracked file names another
program or host, that every import resolves to the standard library, a declared
package or this repository, and that no code loads another program's module by path. `tests/test_stdio_smoke.py` spawns the real
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
tests that spend real money are gated behind `NODE_FABLE_LIVE=1` plus
`NODE_FABLE_LIVE_CONFIG=<config file>` (the config those calls run under) and are
skipped by default. Keep deployment-specific values out of the code and configurable
through the settings above.

## License

MIT. See `LICENSE`.
