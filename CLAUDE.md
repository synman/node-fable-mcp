# node-fable-mcp — developer notes

> Rules for working on `node-fable-mcp`. The user-facing guide is `README.md`.

One-shot headless Fable-model consultations via the cswap account switcher.
FastMCP stdio server exposing exactly two tools: `fable_consult` (spends real
account quota per call) and `fable_headroom` (free read-only eligibility).
A `claude-fable-5-1` consultation spawns `cswap run <account>
--require-session --no-share -- -p --model <m> --output-format json`, EXCEPT
when the selected account is the current default login — cswap cannot swap
into the login it is already on, so that account launches through the same
no-wrapper argv the Opus tiers use. The two Opus fallback tiers never use
the wrapper at all — see the direct-invoke exception under Scope. The brief
is on stdin on every path, never argv.

## Scope

- Role-framed one-shot consultations: adversarial_review, review_diff, plan,
  diagnose, implement.
- Account selection over the deployment-supplied `NODE_FABLE_ACCOUNTS`
  allowlist intersected with the live cswap snapshot; fails CLOSED when unset.
  The rule has four terms : not `disabled`, 5h avail
  > 0, 7d avail > 0, and among those the greatest remaining Fable avail. An
  account cswap reports `disabled` is excluded from selection and still
  disclosed in `fable_headroom`'s rows.
- **`is_active` is not a selection term.** The current default login
  competes on the same four terms as every other account and appears in
  `fable_headroom`'s `selector_would_pick` when it wins. The flag decides
  only the launch path, because `cswap run --require-session` refuses the
  login it is already on. That holds for resume too: the path is chosen from
  the pinned account versus the live login, never from the model tier alone.
- **Opus direct-invoke exception.** `claude-opus-5` and
  `claude-opus-4-8` skip the selector and the cswap wrapper entirely and run
  against whatever account is already the active default login — operator
  decision, so a fallback tier never draws on the isolated 3-account pool.
  Every other invariant is unchanged on that path: scrub chokepoint, write
  guard, call-state, `--add-dir` containment validation, git-write deny.
- **Read-only MCP tool grant.** `--mcp-config`/`--settings`
  are generated per launch from a measured tool census: every server except
  the four named in `mcp_grant.py` (agent-coordination servers) is eligible,
  classified by each tool's `readOnlyHint`.
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
- Ladder state: the fable→opus fallback ladder is CALLER-driven across
  separate calls; the server holds no ladder memory.
- Editing cswap itself (an external project).

## Safety

- Never manufacture a refusal, a 429, or account exhaustion to test a path.
- Never grant `~/.claude` or `~/.claude-swap-backup` — directly or via any
  ancestor; validation checks BOTH containment directions.
- An ARMED call that errors never retries or fails over anywhere
  (`armed_stop` — the target tree may hold a partial mutation).
- Secret-pattern hits REFUSE the call outright, never redact-and-send. This
  governs every path INTO the subprocess — the assembled prompt AND the
  generated `--mcp-config`/`--settings` payloads.
- **A read-only annotation never overrides the name deny.** `mcp_grant.py`
  `NAME_DENIED_TOOLS` denies four tools by qualified name in every payload,
  whatever the census says: `mcp__node-process-mcp__process_list`,
  `mcp__node-process-mcp__detect_orphans`,
  `mcp__node-process-mcp__system_health` and
  `mcp__node-procpool-mcp__session_output`. They are truthful read-only tools,
  and that is not what the deny is about: `process_list` and `detect_orphans`
  return `cmdline` for every visible process (MCP config JSON, Authorization
  headers, passwords passed as arguments), `system_health` carries the first
  five `detect_orphans` rows (cmdline cut at 200 characters) under
  `orphans.top`, and `session_output` returns captured
  tmux pane text, and whatever Fable reads lands in a plaintext transcript under
  the account that ran the call (the README's pitfall 13). So re-measuring the census
  never lifts it; remove an entry only by an an explicit maintainer decision in this tuple. The
  census row carries a read-only count, not names, so a denied tool the row does
  not already list as a write is taken out of that count, and
  `disclosure.tools_denied_by_name` lists the set. Tests: `tests/test_mcp_grant.py`.
- Echoed subprocess output is secret-redacted before it can reach a
  transcript. That is the opposite direction and is the one sanctioned use
  of redact-not-refuse.
- **`--add-dir` is not a path sandbox for Fable's own `Read`.** Measured
  2026-09-08 on all three path classes tried — `/tmp`, `$HOME`, and
  `~/.claude`: an unarmed call read a sentinel in each, outside every
  granted root. `--add-dir` governs what a headless run reaches without an
  interactive prompt, not what `Read`'s path argument may resolve to. This
  predates the MCP tool grant and is independent of it. Test:
  `tests/test_live_reach_dot_claude.py`.
- **Hooks are disabled per launch; CLAUDE.md is not.** `--settings`
  carries `disableAllHooks: true` (2026-09-08). Before that it carried only
  `hooks: {}`, which merges additively and suppresses nothing, so on the
  direct launch path (active login, or the Opus tiers — real HOME, no
  config-dir isolation) the user's own PreToolUse hooks ran inside the
  paid subprocess and denied Grep and every shell git read with a redirect
  the subprocess could not follow. What `disableAllHooks` does NOT do:
  stop the direct path from loading the user's `~/.claude/CLAUDE.md`
  rules corpus into the consultation. No CC flag isolates CLAUDE.md while
  keeping keychain auth (`--bare` drops both), so that residual stays open
  and is a known open limitation.
- **There is no shell git path unarmed.** The four `Bash(git <verb>:*)`
  allow rules were dropped the same day: they were command-prefix rules
  that could not reach a repo from the empty scratch cwd, and the shapes
  that could (`cd <repo> && git …`) are what the headless engine refuses.
  Git reads come from a git MCP server's `readOnlyHint` tools via the MCP tool
  grant.
- **The git-write deny is argv-pattern matching only.** It covers `git
  commit`/`git push` and their `sh -c` forms. It does NOT cover a
  `python -c` or library-level (`dulwich`, `GitPython`) commit, and more
  patterns cannot close that class while armed `Bash` is otherwise
  unrestricted. What bounds it is the write guard itself: `confirmed=True`
  is required before `Bash` is reachable at all.

## Testing

`python3 -m pytest tests/ -q` — no conftest or pyproject; each test file
bootstraps `sys.path`. Live write-guard proofs are `NODE_FABLE_LIVE=1`-gated
and spend real money; the suite skips them by default (state both counts:
passed AND skipped). `evals/verify_eval.py` spawns the real server for
`tools/list`.

Defaults that resolve under `~` (state dir, census, MCP registry, cswap source)
are all overridable through the `NODE_FABLE_*` variables in the README, so the
suite also runs under a scratch `HOME`.

## Client integration

**Claude Code (measured 2.1.266) auto-backgrounds a slow tool call after
~120s** and delivers the result later via a task-notification — no
`MCP_TOOL_TIMEOUT` env var exists in this client's process tree at all, and
raising one client-side does nothing for it. The real hazard: the
notification is lost if the calling session exits before the subprocess
finishes, since only the subprocess survives, not the background-task
handle. Other MCP clients may lack auto-backgrounding and simply time the
call out client-side while the subprocess keeps running and billing — for
those, `MCP_TOOL_TIMEOUT` (milliseconds) IS read from the client session's
own launch env, not this server's env block, and should exceed
`NODE_FABLE_TIMEOUT_S`. Either way, `NODE_FABLE_TIMEOUT_S` is the actual
kill — see README.md's Registration section for the full detail. 
