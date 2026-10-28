# Installing units, versions, deprecation and drift

`skill_eval.py install` copies a unit into the folders each tool reads. `version bump`, `deprecate` and `drift` manage
its versions. Unit B6, plan.md:1356-1413. The install policy is `policy/install.json`, read by `engine/install.py`.

## Project install

```
python3 skill_eval.py install <unit> --target <project folder> [--tools claude,codex,opencode,hermes] [--dry-run] [--force]
```

`--tools` defaults to all four tools and `--scope` defaults to `project`. `--target` is required and must be an
existing folder.

What it copies:

- An agent: the four generated wrappers at `targets/<tool>.json` `wrapper.path` in the agent folder. A missing or
  out-of-date wrapper refuses and names `python3 eva.py generate <agent>`. Install never generates.
- A skill: every regular file of the skill folder. A symbolic link or a special file in the folder refuses.

Where it writes, under the real path of `--target`:

| kind | tool | destination | source |
|---|---|---|---|
| agent | claude | `.claude/agents/<name>.md` | inputs/design.md:46, read 2026-10-19 |
| agent | codex | `.codex/agents/<name>.toml` | inputs/design.md:62, read 2026-10-19 |
| agent | opencode | `.opencode/agents/<name>.md` | inputs/design.md:77, read 2026-10-19 |
| agent | hermes | `.hermes/profiles/<name>/SOUL.md` | UNVERIFIED: only `~/.hermes/profiles/` is recorded |
| skill | claude | `.claude/skills/<name>/` | inputs/design.md:53, read 2026-10-19 |
| skill | codex | `.agents/skills/<name>/` | inputs/design.md:68, read 2026-10-19 |
| skill | opencode | `.opencode/skills/<name>/` | UNVERIFIED (plan.md:1022) |
| skill | hermes | `.hermes/skills/<name>/` | UNVERIFIED (plan.md:1076) |

Every UNVERIFIED destination prints `UNVERIFIED` on its line. The Hermes `config.yaml` is never written, and an agent
install with Hermes prints `hermes: config.yaml UNVERIFIED, not written (gates.md G4)`.

Each destination reads one of three ways:

- `create`: the file is absent.
- `unchanged`: the file holds the same bytes. Nothing is written.
- `overwrite`: the file holds other bytes. Without `--force` this refuses, naming each file. With `--force` the old
  file is first copied to `<target>/.unit-install-backup/<UTC YYYYMMDDTHHMMSSZ>/<path>`, in a fresh folder that gets
  a `-2` style suffix when the stamp exists. Then the new file replaces it atomically.

A different `.md` or `.toml` file in the same agent folder that declares the same agent name (`name:` in the
frontmatter, `name =` in TOML, a trailing `# comment` ignored) refuses even with `--force`. A repeat run prints `unchanged` per file and `no change`, and
writes nothing.

`--dry-run` prints the same lines, a unified diff per overwrite and the summary prefixed with `dry run:`. It writes
nothing. The last line is always the summary:

```
install <name> (<kind>) -> <target>: N create, N overwrite, N unchanged
```

## Destination safety

Two homes count: `$HOME` and the account home from the password database. Under each, the policy lists the forbidden
roots (`.claude`, `.codex`, `.config/opencode`, `.hermes`, `.agents`, `Library/Keychains`, `.ssh`,
`.config/skill-master`) and files (`.claude/settings.json`, `.claude/settings.local.json`, `.claude/hooks`,
`.codex/config.toml`, `.codex/auth.json`). The loader refuses a policy that drops a root or file plan.md:1369 names.
That floor is code in `engine/install.py`, so editing the policy cannot open a root.

A project install refuses when:

- `--target` resolves to a home, to an ancestor of a home, or to a path under a forbidden root.
- A destination resolves outside `--target`. This covers a link inside the target that leads elsewhere.
- A destination resolves under a forbidden root.
- A destination is itself a symbolic link. `--force` does not override this.

Equality and "under" are decided by file identity, not by path strings: this volume folds letter case and reaches
`/private` and `/Users` through `/System/Volumes/Data` too, and `realpath` keeps either spelling. A path lies under a
root when one of its existing folders is the root's deepest existing folder (same device and inode) and the names
below it start with the root's remaining names, compared without case. So `HOME` for `home`, `~/.CLAUDE` and the
firmlink spelling of the home refuse like the plain spelling.

Every check runs before the first write, so a refusal leaves no folder and no backup.

## User scope

```
python3 skill_eval.py install <agent> --scope user --tools claude
```

User scope is Claude agents only (Appendix E, Q12). Any other tool, and any skill, refuses with exit 2. The only
destination is `$HOME/.claude/agents/<name>.md`, taken from `$HOME`. The path is code (`engine/install.py`
`USER_AGENT_PATH`), and the loader refuses a policy whose `user_scope.agent.claude.path` names another. It refuses when
`CLAUDE_CONFIG_DIR` is set, when `$HOME/.claude` or `$HOME/.claude/agents` is a link, and when the destination lies
under a forbidden file.

Order of a user-scope install:

1. The agent's record in its workspace must read `registered` now (`registration_state`), so the G1 coverage gate held
   when it was recorded and nothing has changed since. The wrapper must be up to date.
2. The manifest hash is recorded as an unsigned approval under the gate `install_user` (references/approval-gates.md).
   The manifest is canonical JSON of format 1, scope, tool, unit name, the record's registration hash and each
   destination with its source path and source sha256.
3. The plan is computed again. If its hash differs from the recorded one, nothing is written. Otherwise the record is
   stored under `install_user` in the workspace `approvals.json`, and the file is written by the same rules as a
   project install. A backup goes to `$HOME/.unit-install-backup/`.

`--dry-run` records nothing, writes nothing, and prints the plan and the prerequisite states. A repeat run whose file is
unchanged prints `no change` and records nothing.

## Agent records

`approve <agent> --gate coverage_waiver:<tool>`, `approve <agent> --gate registration` and `register <agent>` take an
AGENT.md v2 folder. The workspace is `<parent>/<name>-workspace` unless `--workspace` names one. For an agent:

- The eval set is the files `scenarios/*.json`.
- `agent report` reads the recorded waivers, so a valid waiver clears the `coverage-missing` problem of its tool only.
- The record has `kind` `agent`. `skill_path` is the agent folder, `skill_sha256` its tree hash, `eval_set_sha256` the
  scenario hash, `skill_md_sha256` the hash of AGENT.md, and `version` the optional AGENT.md key `version` (SemVer).

## version bump

```
python3 skill_eval.py version bump <unit> major|minor|patch [--dry-run]
```

The version is SKILL.md `metadata.version`, else `version`, or the AGENT.md `version`. A missing or non-SemVer version
refuses. The next release raises the part, sets the lower parts to 0 and drops any prerelease or build.

`CHANGELOG.md` at the unit root must hold a line exactly `## <new version>` and a non-blank English line under it
before the next `## ` heading. Otherwise the command exits 2 and writes nothing. On success only the version line of the
frontmatter changes. CHANGELOG.md is an installed file, so the English-only row `english-bundled-files` scans it in a
skill. An agent bump makes the wrappers out of date: run `python3 eva.py generate <agent>` before the next install.

## deprecate

```
python3 skill_eval.py deprecate <unit> --version X.Y.Z --reason TEXT
```

This writes `<workspace>/deprecations.json` as `{"format": 1, "versions": {"X.Y.Z": {"reason": ..., "at": ...}}}`. It
refuses a non-SemVer version, a version above the current one, and a reason that is empty or not printable ASCII. The
same version and reason again is `no change`.

A deprecation is an unsigned marker, not a gate. Installing a deprecated version prints
`warning: <name> <version> is deprecated: <reason>` and installs as usual, so the old version stays installable.

## drift

```
python3 skill_eval.py drift [ROOT] [--observed TOOL=VERSION ...] [--today YYYY-MM-DD]
```

Three sections:

- Tools. Each tool's pinned version from `policy/install.json` `tool_pins` (claude 2.1.302, codex 0.162.1, opencode
  1.17.13, read 2026-10-19 at inputs/design.md:225; Hermes not installed). drift starts no tool binary. Without
  `--observed` a tool reads `observed unknown`. A different observed version reads `DRIFT`.
- Units. Every unit under ROOT whose recorded `ruleset_sha256` differs from the current one, read from
  `registration.json`, else the last iteration's `benchmark.json`, else none.
- Rules. Every rule whose `source.verified` date is more than `RULE_VERIFIED_MAX_DAYS` days (policy/eval-policy.json,
  180) before today, or null.

The exit code is 3 when anything is listed, else 0. drift reads files only. It starts no process and opens no socket.

The pins live in `policy/install.json` and not in `targets/*.json`, because `schemas/targets.schema.json` is closed.

## Known limits

- An overwrite of a skill folder replaces the files the skill has now. A file an older version had and the new one
  dropped stays in the destination.
- Whether Hermes reads a project profile, and the OpenCode and Hermes skill folders, are UNVERIFIED.
