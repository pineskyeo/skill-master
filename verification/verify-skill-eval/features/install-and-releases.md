# Install and releases

`skill_eval.py install` copies a skill folder or an agent's wrappers into the folders each tool reads. The neighboring commands manage versions and show what changed: `version bump`, `deprecate`, `drift`, `hub build`, `snapshot` and `builder-check`.

## Sub-features

- `install-skill` copies every regular file of a skill per tool.
- `install-agent` copies the four wrappers.
- `install-safety` refuses a home folder, a link, a collision without `--force`, and a missing `--target`.
- `version-bump` raises the version after a `CHANGELOG.md` entry.
- `deprecate` writes an unsigned marker, and install warns about it.
- `drift` lists tool, unit and rule drift.
- `hub` writes the static registry site.
- `snapshot` copies the skill to its workspace.
- `builder-check` prints the writable set and a fingerprint of the rest.

## How to get to it (user POV)

- Run `python3 skill_eval.py install UNIT --target FOLDER [--tools LIST] [--dry-run] [--force]`.
- Run `python3 skill_eval.py version bump UNIT patch [--dry-run]`.
- Run `python3 skill_eval.py deprecate UNIT --version X.Y.Z --reason TEXT`.
- Run `python3 skill_eval.py drift ROOT [--observed TOOL=VERSION]`.
- Run `python3 skill_eval.py hub build ROOT --out FOLDER`.
- Run `python3 skill_eval.py snapshot SKILL`.
- Run `python3 skill_eval.py builder-check SKILL --workspace WS [--compare SAVED]`.

## Driving it with drive.py

Preconditions:

- `drive.py doctor` prints `doctor: ready`.
- `--target` must be a folder. The scratch `target/` folder exists from the start.

- **Dry run, then install.** Run `drive.py journey unit-lifecycle`. `install ... --dry-run` prints `dry run: install csv-cleaner-en (skill) -> ` and `lacks` proves `../target/.claude` is absent. The real row prints `install csv-cleaner-en (skill) -> ...: N create, 0 overwrite, 0 unchanged`. `same` proves `SKILL.md` has the same bytes in `../target/.claude/skills/csv-cleaner-en/`.
- **Repeat.** The next `install` prints `no change`.
- **Safety.** `--target {scratch}/home` exits 2 and prints `is the home folder`. No `--target` exits 2. A changed destination exits 2 and prints `collision:`. `--force` prints `backup .../.unit-install-backup/<stamp>`, and `same` proves the file is restored.
- **A link in the folder.** With `scripts/out` linked to `/etc/hosts`, `install` exits 2 and prints `scripts/out is a symbolic link`. `snapshot` exits 2.
- **Bump.** Without a changelog, `version bump` exits 2. After the `put` row, `--dry-run` prints `dry run: would bump csv-cleaner-en 1.0.0 -> 1.0.1` and `holds` proves the file still says `1.0.0`. The real row prints `bumped` and `holds` proves `1.0.1`.
- **Deprecate.** The row prints `an unsigned marker`, and the repeat prints `no change`. `9.0.0` exits 2 with `above the current version`. The next `install` prints `warning: csv-cleaner-en 1.0.1 is deprecated: superseded`.
- **Drift.** `drift . --today 2026-10-30` exits 3 and prints `tool claude: pinned 2.1.302`. `--observed codex=0.0.1` prints `DRIFT`.
- **Hub.** `hub build` prints `hub build: 1 units`, and `has` proves `index.html`, `registry.json` and `style.css`. A second build exits 2 with `must be absent or an empty folder`.
- **Snapshot and builder-check.** `snapshot` prints `snapshot -> .../fixtures/skills/csv-cleaner-en-workspace/skill-snapshot`. A second one exits 2. `builder-check` prints the writable set and `fingerprint`. `--compare` prints `compare: 0 changed`. After `put` changes `evals/trigger_evals.json`, it exits 1 and prints `changed evals/trigger_evals.json`.
- **The agent install.** Run `drive.py journey agent-lifecycle`. The last `install` prints `hermes: config.yaml UNVERIFIED, not written` and `4 create`. `has` proves the four destinations.

## Gotchas

- The skill's install folder is `.claude/skills/<frontmatter name>/`. The draft of this skill looked in `csv-cleaner`, which is the golden name. The fixture's name is `csv-cleaner-en`.
- `builder-check --workspace` needs an existing folder. It exits 2 with `is not a folder` before any workspace exists, so the journey runs it after `snapshot`. The draft's argument had the right name and the wrong order.
- `install` ships `FEEDBACK.md`, `FEEDBACK.json` and all of `evals/`. See [Hash binding](./hash-binding.md) for what that costs.
- A project install reads no approval. `references/install.md` lists `--scope user` as the gated form, and that form is not driven here.
- `drift` exits 3 on this repository because every unit reads `recorded none`. The exit is expected, so read the lines.
