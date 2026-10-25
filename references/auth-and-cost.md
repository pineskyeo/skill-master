# Auth profiles, the hard cap and spend records

This page is for the operator of `skill_eval.py`. It covers the two auth profiles, the hard USD cap of API-key mode, the spend ledger `<workspace>/spend.jsonl`, crash recovery, how `report` shows actual spend, and what is still unverified.

## The two profiles

`policy/auth-profiles.json` holds exactly two profiles. A command picks one only through `--auth-profile NAME`. The engine never reads a variable, a file or a host name to choose one.

| | personal-mac | company |
|---|---|---|
| billing | subscription | api_key |
| account shim marker | yes | no |
| `claude auth status` and the Codex ChatGPT login check | yes | no |
| API-key variables allowed | none | the names listed per engine (shipped: `ANTHROPIC_API_KEY` for Claude) |
| gateway base URLs allowed | none | the URLs listed per engine (shipped: none) |
| engines without a per-call cap (Codex) and the Codex grader | allowed under `--no-strict-budget` | always refused |
| Claude calls (engine, grader, builder) | allowed, each with `--max-budget-usd` | always refused, before the call starts |
| `--budget-usd` | required for a live start | required, and it is a hard cap |

- `plan` and `estimate` use `personal-mac` when the flag is absent and say so in the `auth profile:` line.
- Every live start (`run`, `grade`, `calibrate`, `loop`, with or without `--dry-run`) needs the flag. Without it the start stops before anything is signed.
- `personal-mac` behaves exactly as before H3. The loader refuses a file whose `personal-mac` differs from the shipped values.
- The file is outside the rule bundle, so editing it does not move `ruleset_sha256` and does not invalidate approvals. A `--live` signature covers the file's bytes, so an edit between signing and start refuses the start.

### No Claude call under company (plan.md:902)

Under `company` every Claude call is refused, whatever its `--max-budget-usd`. That covers the exec, trigger, grader, pairwise, calibrate and builder calls. The reason is that `--max-budget-usd` is not an enforceable per-call maximum: operator lane 8b reported $0.161712 against a $0.01 per-call cap. Claude is also not used through the Anthropic API at the company. pinesky chose this on 2026-10-25 as option (c) for R19-f1 (decisions.tsv 2026-10-25T00:10:05Z and 00:32:52Z). The rule is code in `engine/spend.py` (`API_KEY_REFUSED` and `api_key_refusal`), as the Codex rule is, and `policy/auth-profiles.json` is unchanged. It is checked in three places:

- `plan` lists the refusal in its `budget enforcement:` line and still exits 0. A live start refuses in its `strict-budget` gate before anything is signed, with `error: claude: refused in API-key mode (auth profile company): --max-budget-usd is not an enforceable per-call cap; refusing it` (`claude grader:` or `claude builder:` for those calls) and exit 2. A Codex refusal, when there is one, is shown first. No file is written.
- The ledger raises the same refusal before it writes any row.
- `live_exec` refuses to start a `claude` process in API-key mode, even for a caller without a ledger.

Since Codex is refused as well, no engine the harness knows can make a paid call under `company` today.

### Listing a key, a provider or a gateway (gates.md G23)

Only pinesky decides what `company` lists. To list something, edit `profiles.company.engines.<engine>`:

- `api_key_env`: names from the `API_KEY_ENV` list of `skill_eval.py` (for example `ANTHROPIC_API_KEY`). A set name that no engine lists is refused. A listed name is copied into the child environment of that engine only. The copy adds `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` to a Claude child together with the key, and a value of that variable in your own environment cannot turn it off. The agent under test keeps its Bash tool, and its hooks and the skill's scripts run under it, so they must not inherit the key. The account shim follows the same rule for its account token. No Claude child starts under `company` today (plan.md:902), so this copy and the scrub setting are defence in depth: they hold again if the Claude rule is ever lifted. gates.md G27 (whether the real CLI strips the key from those children) is resolved as not applicable since 2026-10-25.
- `gateways`: a map from a `*_BASE_URL` name of `PROVIDER_ENV` (for example `ANTHROPIC_BASE_URL`) to a list of `https://host[:port][/path]` URLs without user info, query or fragment. A set base-URL variable is allowed only when its value equals a listed URL (one trailing slash is ignored). This compare is the only place a variable's value is read. Values are never printed, logged or written to a record.
- Every other provider-routing variable (Bedrock, Vertex, Foundry selectors, profiles) is refused under `company`. Listing one needs a code change, because the profile file only accepts key names and base URLs.

## The hard cap

No Claude or Codex call starts under `company`, so no engine the harness knows can make a paid call there today. The cap, halt and overshoot rules below stay in the code. They apply to an engine with an enforceable per-call bound, which none is today.

Under `company`, such a call starts only when both hold:

- its worst-case bound is at least $0.01;
- the spend so far plus that bound is at most `--budget-usd`.

| call kind | worst-case bound |
|---|---|
| a call of an engine with an enforceable per-call cap | that per-call cap |
| any Claude call (exec, trigger, grader, pairwise, calibrate, builder) | not admitted: refused in API-key mode (`--max-budget-usd` is not an enforceable per-call cap) |
| Codex engine, Codex grader | none enforceable: refused in API-key mode |

Under `personal-mac` each Claude call still gets `--max-budget-usd`, which is `CLAUDE_CAP_HEADROOM` (0.5) of the remaining budget, floored to cents.

The cap is per command. It is the signed `--budget-usd` of that command, and the bounds of crashed calls that this command recovers count against it. There is no cap across the commands of one workspace.

A call stops the command when the CLI reports a cost above its per-call cap (an overshoot), or when the spend goes above the hard cap. A call whose CLI reports no valid `total_cost_usd` (missing, null, negative, not finite or not a number) has an unknown real cost: it is charged at least its per-call cap (`charged_source` `cap_charged_unreported` when the cap is more than the computed charge), and it stops the command too. Every later call of that command is refused with a reason that names the overshoot, the excess or the unreported cost. Under `run` the first refused exec or trigger call ends the iteration (`plan.json` `stopped`); `loop` then returns `run-stopped`. When the stopping call is the last paid call of the command, the command checks once more after it: `run` records the reason in `plan.json` `stopped` and logs `STOPPED:`, `grade` and `calibrate` exit 2 with `error: <reason>` (`calibrate` writes no `calibration.json`), and `loop` returns `run-stopped` with the reason. Under API-key billing a `run` whose iteration stopped, for any reason, exits 3; under `personal-mac` it exits 0. No `company` run reaches that point today, because its start refuses every engine.

## The spend ledger

Every paid call of a live command, under both profiles, writes two rows to `<workspace>/spend.jsonl`:

- an intent row right before the CLI starts (or at once, when the call is refused);
- a result row where its cost is charged.

Each row is one JSON line, appended with one `write` and an `fsync`. Creating the file also fsyncs the workspace folder. The file has mode 0600. USD values are rounded to six decimals.

Every row has `v`, `type`, `call_id` (`<run_id>:<seq>`), `run_id`, `pid`, `at`, `command`, `kind` (exec, trigger, grader, pairwise, calibrate, builder), `engine`, `model`, `profile`, `billing`, `iteration`, `cap_usd` (the hard cap) and `remaining_usd`.

- `intent` adds `decision` (allowed or refused), `reason`, `spent_before_usd`, `call_cap_usd` and `bound_usd`.
- `result` adds `outcome` (ok, error, launch_failed, unparsed, not_launched), `reported_cost_usd` (the CLI's `total_cost_usd`), `input_tokens`, `output_tokens`, `computed_cost_usd` (tokens x price), `price`, `price_source` (the pricing row's source URL, `--price`, or `assumed`), `price_date`, `charged_usd`, `charged_source`, `spent_after_usd` and `overshoot_usd` (reported cost minus the per-call cap, at least 0; null without a cap or a reported cost).
- `recovered` adds `recovers` (the crashed call's `call_id`) and `counted_usd` (its `bound_usd`).

A row that a call writes while the loop builder runs is recorded as a harness write, so it does not read as `builder-wrote-outside`. A builder that writes `spend.jsonl` itself still does.

## Crash recovery

An allowed intent with no result row and no recovered row is a dangling intent. It means the harness died during that call. Both profiles recover. Under `company` no call is admitted today, so a `company` dangling intent can only come from a ledger written before the Claude refusal (plan.md:902) or edited by hand.

1. `plan`, `--live --dry-run` and the signing summary read the ledger without creating or locking it. When there are dangling intents they print `spend ledger: N dangling intent(s) from crashed calls, $X counted as spent at the next live start`, and the budget gates compare the estimate with the budget minus `$X`.
2. After the start is signed and live mode is on, the command opens the ledger and takes an exclusive `flock`. If another live command holds it, the start refuses with `another live command is using <ws>/spend.jsonl`.
3. It reads the file again and refuses when the dangling intents differ from the ones it previewed.
4. It writes one `recovered` row per dangling intent and starts its spend at their sum. Each crashed call is counted once.

The reader ignores a torn last line (no trailing newline, or not JSON) and reports it. The recovering command cuts that line off before it appends. Any other bad line, or a row that misses a field, stops the command with the line number. So does a row whose `call_id` (or `recovers`) is not a non-empty string, an intent whose `decision` is not `allowed` or `refused`, and a row whose summed amount is negative, not finite or not a number (`bound_usd`, `spent_before_usd` and `call_cap_usd` of an intent, `reported_cost_usd` and `charged_usd` of a result, `counted_usd` of a recovered row; each may be null). A live start then exits 2 before anything is signed, and `plan` and `report` fail closed the same way. `remaining_usd` is not checked, because it is negative after an overshoot.

## Actual spend in reports

`report` reads the ledger rows of the iteration.

- With API-key result or intent rows (an intent alone, as of a crashed call, counts), `benchmark.json` `cost` gets `billing` `api_key`, `actual_spend_usd` (the sum of the CLI-reported costs), `actual_spend_basis` `cli_reported`, `unreported_calls`, `unreconciled_calls`, `unreconciled_bound_usd` and `caps`. `comparison.md` ends its cost header with `actual spend: $Y (CLI-reported, API-key mode, auth profile company); hard cap: $Z (--budget-usd of run)` and adds `| actual spend | $Y |` and one `| hard cap | $Z (command) |` row per cap.
- With subscription rows only, the report is unchanged: `actual_spend_usd` stays null and spend is "not metered".
- With both, `billing` is `mixed` and only the API-key rows count.

Reconciling these figures with a billing console needs network access and is not part of the harness.

## Unverified

- How the account shim on `PATH` treats API-key variables. Under `company` the harness sends no shim marker and never consults the shim's account folders; the shim itself is not inspected.
- Whether `os.fsync` flushes the drive cache on macOS. The ledger uses `os.fsync`, not `F_FULLFSYNC`, so a power loss right after a write may lose the last rows.

Two earlier items are settled by option (c) for R19-f1 (plan.md:902). The `apiKeySource` value of a real API-key Claude call no longer matters, because no Claude call runs under `company`; the check of it (gates.md G25) stays in the code. How far a call can overshoot `--max-budget-usd` (plan.md Appendix C, R16) was measured: operator lane 8b reported $0.161712 against a $0.01 per-call cap. That overshoot is the reason Claude is refused in API-key mode.

## Operator lanes 6 to 8

These lanes are pinesky's and never run in an agent session (plan.md:922-924).

- Lane 6 is now a refusal check. Put a stub `claude` early on `PATH` and run `run --live --auth-profile company --budget-usd 0.05` on a temp fixture. Expect the start to refuse Claude before any call starts, with the refusal message on stderr and a non-zero exit, and expect no paid call and no spend record (`spend.jsonl` is not created). `tests/test_spend_ledger.py` and `tests/test_auth_profiles.py` reproduce it with a stub and with the real `live_exec` over a recorder.
- Lane 7 is not applicable. No company call can start, so none can crash mid-call. Crash recovery stays covered by `tests/test_spend_ledger.py` on temp fixtures.
- Lane 8 is not applicable. No real Claude call runs in API-key mode. Its 2026-10-24 capture `real-api-call.txt` is kept as the evidence for the decision.
