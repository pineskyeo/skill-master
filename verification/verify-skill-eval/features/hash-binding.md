# Hash binding

A recorded approval binds a set of digests, so an approval stops counting when the bound content changes. A user relies on this every time `plan` reads `approved` or a pinned skill reads `registered`. Two defects weaken that promise. The repro scripts record them as open findings. `drive.py all` does not cover them.

## Sub-features

- `tree-digest` is `skill_tree_hash`, over the `eva.unit_tree` files of a folder.
- `eval-digest` is the `eval_set` gate hash, over `evals.json`, `trigger_evals.json` and the listed fixtures.
- `waiver-digest` is a coverage waiver hash over the tool, the tree digest and the eval digest.
- `framing` is how `sha256_labeled` separates a label from its content. This is candidate A.
- `install-gap` is the set of files `install` ships outside every digest. This is candidate B.

## How to get to it (user POV)

- Read the gate state with `python3 skill_eval.py plan SKILL`, in the lines `approval gates:` and `waivers:`.
- Read the gate hashes with `python3 skill_eval.py pending --unit UNIT`.
- Read a fixture digest with `python3 skill_eval.py agent plan AGENT`, in the line `fixture <id> <slug>: sha256 <hash>`.
- Ship a unit with `python3 skill_eval.py install UNIT --target FOLDER`.

## Driving it with drive.py

Preconditions:

- `drive.py doctor` prints `doctor: ready`.
- Each repro makes its own scratch folder. The script writes the approval records in A1c and A3d by hand into that folder, the way `tests/test_agent_eval.py` writes `approvals.json`, and the real `plan` reads them back.

- **Candidate A.** Run `python3 verification/verify-skill-eval/repro_sha256_labeled.py`. The overall line reads `REPRODUCED candidate A: 12 of 15 defect sub-claims hold`.
- **One file against two.** A1 prints one `skill_tree_hash` for `a=x\0b\0y` and for `a=x`, `b=y`. `eva.py check` gives PASS to both.
- **The same names.** A1b prints one digest for `a=x`, `b=p\0b\0q` and for `a=x\0b\0p`, `b=q`.
- **A recorded approval survives.** A1c prints `approved by verify-fixture` before and after the split, and `NOT APPROVED (stale)` on a real edit.
- **The sentinels.** A2 shows a file holding `<missing>` matches a dangling link. A2c shows it matches an absent listed file through the eval digest. A3 shows a file holding `<outside>` matches a link out of the folder with no NUL byte, when the file is last in walk order. A3c shows the same for `<cycle>`. A3d shows a recorded waiver survives that swap.
- **Other digests.** A5 swaps bytes between two listed fixtures under one eval digest. A6 prints one fixture digest for one and for two files. A7 does the same through `load_bundle`.
- **Candidate B.** Run `python3 verification/verify-skill-eval/repro_install_unhashed.py`. The overall line reads `REPRODUCED candidate B: 12 of 13 defect sub-claims hold`.
- **Each file class.** For `evals/` entries, `__pycache__`, `*-workspace`, `.git` and `FEEDBACK.*`, at the root and nested, the script shows `install exit 0`, the same bytes in the target, and `digests moved: none (of 11 compared)`.
- **Tested against shipped.** B2 shows a file in the install target and absent from the sandbox an eval run builds.
- **Project scope.** B3 shows install succeeds with no workspace and with garbage in `approvals.json` and `registration.json`. The hook saw that second install open neither file. As the control, `plan` and `drift` on the same two files log both opens in the same audit, and the script crashes if they do not.
- **The reverse.** B4 reads `REFUTED`. No hashed file is left out of a successful install. A link or a pipe makes `install` refuse the whole folder.
- **Verdicts are observed.** Run `drive.py flip`. Both overall lines read `REFUTED ... 0 of` on a scratch copy with the framing and `skill_files` fixed.

## Gotchas

- The framing has more than one copy. `sha256_labeled` lives in `skill_eval.py:280` and `engine/rule_runner.py:33`, and `tests/test_rule_runner.py:733` asserts the two sources are equal. `agent_bound_files` near `skill_eval.py:5437` repeats the framing inline. A fix must touch all three. `drive.py flip` patches only the first two, which is enough for the repro claims and not enough for a release.
- A NUL in a label gives no collision through a gate. `evals.json` holds the `files` list and hashes first, and AGENT.md holds the name and hashes first, so a changed list or name moves the digest before the framing matters. A4c reads `REFUTED`. The defect is a NUL in file content, which `os.walk` labels cannot prevent.
- Order matters for the markers. Pairs hash first, then marked entries, so a regular file matches a link only when it is the last pair of the walk. A3b reads `REFUTED` at the folder root.
- A swap to a link fools a stored record, not a copy. `eva.py check`, `install` and `snapshot` each refuse the link on their own walk. A dangling link is different, because `eva.py check` gives it PASS.
- An absent file does not collide through `skill_tree_hash`. It has no pair. A2b reads `REFUTED`. It collides through the eval digest because `eval_set_files` always lists its paths.
- The word `installed` means two things. An eval run copies the `eva.unit_tree` files. The `install` command copies every regular file. The `skill_tree_hash` docstring and the English rule label both use the first meaning.
- The English-only scan shares the ignore list, so the same file classes also skip the scan. B1 is the control. The same bytes at a hashed path turn the verdict to FAIL.
- A project install asks for no approval, so candidate B is no bypass at that scope. It means a recorded approval or an eval result says nothing about the extra files `install` ships.
- The three real skills install 5 to 10 files and `skill_tree_hash` covers one of them, `SKILL.md`. The eval digests bind every other file, so no shipped skill carries an unbound file today. B6 reads `REFUTED` and prints the counts. The defect is open for the next file a skill adds.
- The execute bit installs and moves no digest. B5 reports it and keeps it out of the overall verdict, because it is an attribute and not a file class.
