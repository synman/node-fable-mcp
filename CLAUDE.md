# node-fable-mcp — developer notes

> Rules for working on `node-fable-mcp`. The user-facing guide is `README.md`.

One-shot headless Fable-model consultations through a configured command and an
optional account switcher. FastMCP stdio server exposing exactly two tools:
`fable_consult` (spends real account quota per call) and `fable_headroom` (free
read-only eligibility). A default-model consultation spawns the
`switcher_command` template followed by the `command_args` template, EXCEPT when
the selected account is the current default login — the switcher cannot swap
into the login it is already on, so that account launches through the same
no-wrapper argv the fallback tiers use. The fallback tiers (every configured model
but the default) never use the wrapper at all — see the direct-invoke exception
under Scope. The brief is on stdin on every path, never argv.

Everything that depends on the machine is a setting (`node_fable/settings.py`:
environment variable `NODE_FABLE_<KEY>`, then the JSON config file, then a neutral
default); the README has the settings table. Nothing in this tree names a command,
a model, a tool or a directory of any particular machine. A required setting that is
unset refuses the call before anything launches ("not configured"), so it spends
nothing; the safety lists (`forbidden_roots`, `git_write_deny_tools`,
`excluded_servers`, `denied_tools`) refuse when unset and accept an explicit `[]`.

## Scope

- Role-framed one-shot consultations: adversarial_review, review_diff, plan,
  diagnose, implement.
- Account selection over the deployment-supplied `NODE_FABLE_ACCOUNTS`
  allowlist intersected with the live accounts snapshot; fails CLOSED when unset.
  The rule has four terms : not `disabled`, 5h avail
  > 0, 7d avail > 0, and among those the greatest remaining Fable avail. An
  account the snapshot reports `disabled` is excluded from selection and still
  disclosed in `fable_headroom`'s rows.
- **`is_active` is not a selection term.** The current default login
  competes on the same four terms as every other account and appears in
  `fable_headroom`'s `selector_would_pick` when it wins. The flag decides
  only the launch path, because the switcher refuses the
  login it is already on. That holds for resume too: the path is chosen from
  the pinned account versus the live login, never from the model tier alone.
- **Direct-invoke exception.** Every configured model but the default skips the
  selector and the switcher wrapper entirely and runs against whatever account
  is already the active default login — operator decision, so a fallback tier
  never draws on the isolated account pool. Every other invariant is unchanged
  on that path: scrub chokepoint, write guard, call-state, directory-grant
  containment validation, git-write deny.
- **Read-only MCP tool grant.** The MCP-config and settings payloads (from the
  `mcp_config_template` and `settings_template` settings) are generated per
  launch from a measured tool census: every registered server except the ones
  named in the `excluded_servers` setting (agent-coordination servers) is
  eligible, classified by each tool's `readOnlyHint`.
  Write and unclassified tools are denied by bare exact name, which removes
  them from the subprocess's context rather than blocking at call time.
- **`adversarial_review` may resume its own prior session.**
  `review_diff` may not, and keeps the unconditional self-review rejection.
- A shared prompt fragment describing that granted read-only surface.
- The scrub chokepoint (secret refusal + jargon neutralization) on every
  assembled prompt before any spawn.
- The write guard: unarmed read-only by default; `confirmed=True` arms;
  git commit/push denied in both states; `git_write_override` is
  operator-only, per call.

## Non-Scope

- Interactive or persistent Fable sessions.
- Ladder state: the model fallback ladder (the configured models, in order) is CALLER-driven across
  separate calls; the server holds no ladder memory.
- Editing the account switcher or the accounts command (external programs).

## Safety

- Never manufacture a refusal, a 429, or account exhaustion to test a path.
- Never grant a path in the `forbidden_roots` setting (a configuration tree, a
  credential backup) — directly or via any ancestor; validation checks BOTH
  containment directions. Unset is refused when a path is granted; `[]` is a
  deliberate none.
- An ARMED call that errors never retries or fails over anywhere
  (`armed_stop` — the target tree may hold a partial mutation).
- Secret-pattern hits REFUSE the call outright, never redact-and-send. This
  governs every path INTO the subprocess — the assembled prompt AND the
  generated MCP-config and settings payloads.
- **A read-only annotation never overrides the name deny.** The `denied_tools`
  setting names tools by qualified name (`mcp__<server>__<tool>`) that are denied
  in every payload, whatever the census says. They are truthful read-only tools,
  and that is not what the deny is about: a process-listing tool returns the full
  command line of every visible process (MCP config JSON, Authorization
  headers, passwords passed as arguments), a health tool can embed the first rows of
  that listing, and a terminal-capture tool returns captured pane text, and
  whatever Fable reads lands in a plaintext transcript under the account that ran
  the call (the README's pitfall 13). So re-measuring the census never lifts it;
  remove an entry only by an explicit maintainer decision in the setting. The census row
  carries a read-only count, not names, so a denied tool the row does not already
  list as a write is taken out of that count, and `disclosure.tools_denied_by_name`
  lists the set. Tests: `tests/test_mcp_grant.py`.
- Echoed subprocess output is secret-redacted before it can reach a
  transcript. That is the opposite direction and is the one sanctioned use
  of redact-not-refuse.
- **A directory grant is not a path sandbox for Fable's own file reads.** Measured
  2026-09-08 on all three path classes tried — a temp directory, the home
  directory, and a configuration tree: an unarmed call read a sentinel in each,
  outside every granted root. A directory grant governs what a headless run
  reaches without an interactive prompt, not what a read tool's path argument may
  resolve to. This predates the MCP tool grant and is independent of it. Test:
  `tests/test_live_reach_forbidden_root.py`.
- **Hooks are disabled per launch; the user's rules corpus is not.** The
  `settings_template` payload should switch hooks off at every scope (2026-09-08).
  Before that it carried only an empty `hooks` object, which merges additively and
  suppresses nothing, so on the direct launch path (active login, or the fallback
  tiers — real HOME, no config-dir isolation) the user's own pre-tool hooks ran
  inside the paid subprocess and denied search and every shell git read with a
  redirect the subprocess could not follow. What switching hooks off does NOT do:
  stop the direct path from loading the user's own rules corpus into the
  consultation. No flag isolates it while keeping keychain auth, so that residual
  stays open and is a known open limitation.
- **There is no shell git path unarmed.** Shell command-prefix allow rules for git
  were dropped the same day: they were command-prefix rules that could not reach a
  repo from the empty scratch cwd, and the shapes that could (`cd <repo> && git …`)
  are what the headless engine refuses. Git reads come from a git MCP server's
  `readOnlyHint` tools via the MCP tool grant.
- **The git-write deny is argv-pattern matching only.** It covers `git commit`/`git push`
  and their `sh -c` forms. It does NOT cover a
  `python -c` or library-level (`dulwich`, `GitPython`) commit, and more
  patterns cannot close that class while armed shell access is otherwise
  unrestricted. What bounds it is the write guard itself: `confirmed=True`
  is required before the shell is reachable at all.

## Testing

`python3 -m pytest tests/ -q` — `tests/conftest.py` clears every `NODE_FABLE_*`
variable and points the config file at a nonexistent path for each test, and the
`fake_cli` fixture supplies neutral fake settings; there is no pyproject. Live
write-guard proofs are `NODE_FABLE_LIVE=1`-gated (plus
`NODE_FABLE_LIVE_CONFIG=<config file>`) and spend real money; the suite skips them
by default (state both counts: passed AND skipped). `evals/verify_eval.py` spawns
the real server for `tools/list`.

Defaults that resolve under `~` (state dir, census, source-roots file) are all
overridable through the settings in the README, so the suite also runs under a
scratch `HOME`.

## Client integration

Some MCP clients move a slow tool call to a background task after about two minutes
and deliver the result later. The real hazard: the notification is lost if the
calling session exits before the subprocess finishes, since only the subprocess
survives, not the background-task handle. Other MCP clients may lack that and simply
time the call out client-side while the subprocess keeps running and billing — for
those, the client's own tool timeout (set in the client's launch environment, not
this server's env block) should exceed `timeout_s`. Either way, `timeout_s` is the
actual kill — see README.md's Registration section for the full detail.
