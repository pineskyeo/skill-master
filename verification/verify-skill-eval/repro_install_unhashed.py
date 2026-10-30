#!/usr/bin/env python3
"""Candidate B. install copies every regular file of a skill folder, while skill_tree_hash hashes only eva.unit_tree files.
Prints one verdict line per sub-claim and one overall line, then exits 0. A crash exits 2 and prints neither
verdict word. Standard library only."""
import sys
sys.dont_write_bytecode = True
import hashlib, os, stat
import drive
from drive import Claim, write_bytes as write

NAME = "csv-cleaner-en"
TOOLS = ("claude", "codex", "opencode", "hermes")
RECORDS = ("approvals.json", "registration.json")
PAYLOAD = ("unhashed payload " + chr(0xC548) + chr(0xB155) + "\n").encode("utf-8")
CLASSES = (("evals-unlisted", "evals/notes.txt"), ("evals-nested", "scripts/evals/helper.py"), ("evals-as-file", "scripts/evals"),
           ("pycache-root", "__pycache__/x.pyc"), ("pycache-nested", "scripts/__pycache__/clean.cpython-39.pyc"),
           ("workspace-folder", "scripts/run-workspace/tool.py"), ("workspace-file", "notes-workspace"),
           ("git-folder", ".git/hooks/post-checkout"), ("git-file", "scripts/.git"),
           ("feedback-md", "FEEDBACK.md"), ("feedback-json", "scripts/FEEDBACK.json"))
EDITS = (("SKILL.md", "SKILL.md"), ("scripts/clean.py", "scripts/clean.py"), ("evals/evals.json", "evals/evals.json"),
         ("a listed evals/files fixture", "evals/files/orders_2025.csv"), ("agents/openai.yaml", "agents/openai.yaml"),
         ("workspace requirements.md", "../%s-workspace/requirements.md" % NAME))


def skill_with_no_empty_digest(lab):
    d = lab.skill()
    write(os.path.join(d, "agents", "openai.yaml"), b"interface:\n  display_name: X\n")
    write(os.path.join(d, "..", NAME + "-workspace", "requirements.md"), b"# Requirements\n\nRemove duplicate rows.\n")
    return d


def digests(lab, d):
    se, ws = lab.se, os.path.join(os.path.dirname(d), NAME + "-workspace")
    spec = {"skill_dir": d, "skill_name": NAME}
    a = se.build_parser().parse_args(["plan", d])
    a.argv = ["plan", d]
    out = {"skill_tree_hash": se.skill_tree_hash(d), "skill_md_sha256": se.sha256_files(os.path.join(d, "SKILL.md")),
           "live_plan_hash": se.live_plan_hash(a, spec, ws)}
    for gate in ("eval_set", "requirements", "requirements_eval_set", "rule_review"): out[gate] = se.gate_hash(gate, spec, ws)
    for tool in TOOLS: out["coverage_waiver:" + tool] = se.gate_hash("coverage_waiver:" + tool, spec, ws)
    empty = [k for k, v in out.items() if v is None]
    if empty: raise RuntimeError("vacuous digest, no value to compare: %s" % empty)
    return out


def controls(lab):
    moved, lines = {}, []
    for label, rel in EDITS:
        d = skill_with_no_empty_digest(lab)
        before = digests(lab, d)
        p = os.path.normpath(os.path.join(d, rel))
        with open(p, "ab") as f: f.write(b"\n")
        after = digests(lab, d)
        changed = sorted(k for k in before if before[k] != after[k])
        for k in changed: moved.setdefault(k, label)
        lines.append("edit %s moves: %s" % (label, ", ".join(changed)))
    still = sorted(set(digests(lab, skill_with_no_empty_digest(lab))) - set(moved))
    if still: raise RuntimeError("no hashed edit moved %s" % still)
    return Claim("B0-digests-can-move", True, "each of the %d digests moves on a hashed edit" % len(moved), lines,
                 "This is the control for every comparison below. A listed evals/files fixture moves the eval_set digests, so only an unlisted file of evals/ is unbound.", False)


def installed(lab, d):
    target = lab.folder("target")
    os.makedirs(target)
    p = lab.tool("eval", "install", d, "--target", target, "--tools", "claude")
    return p, os.path.join(target, ".claude", "skills", NAME)


def file_class(lab, cid, rel):
    d = skill_with_no_empty_digest(lab)
    before, verdict_before = digests(lab, d), lab.verdict_of(d)
    write(os.path.join(d, *rel.split("/")), PAYLOAD)
    after, verdict_after = digests(lab, d), lab.verdict_of(d)
    p, dest = installed(lab, d)
    copy = os.path.join(dest, *rel.split("/"))
    same_bytes = os.path.isfile(copy) and drive.sha256(copy) == hashlib.sha256(PAYLOAD).hexdigest()
    moved = sorted(k for k in before if before[k] != after[k])
    holds = p.returncode == 0 and same_bytes and not moved
    return Claim("B-" + cid, holds, "%s installs and moves no digest" % rel,
                 ["install exit %d, file in target with the same bytes: %s" % (p.returncode, same_bytes),
                  "digests moved: %s (of %d compared)" % (", ".join(moved) or "none", len(before)), "before " + verdict_before, "after  " + verdict_after],
                 "An eval run copies unit_tree files only, so it never held this file. The English-only scan uses the same ignore list.")


def scan_control(lab):
    d = skill_with_no_empty_digest(lab)
    before = lab.verdict_of(d)
    write(os.path.join(d, "scripts", "hashed.txt"), PAYLOAD)
    return Claim("B1-scan-control", "PASS" not in lab.verdict_of(d), "the same bytes at a hashed path change the eva.py check verdict",
                 ["before " + before, "after  " + lab.verdict_of(d)],
                 "This shows the English-only rule catches the payload, so a verdict that stays the same in a class above means the scan skipped the file.", False)


def run_versus_install(lab):
    d = skill_with_no_empty_digest(lab)
    rel = "scripts/evals/helper.py"
    write(os.path.join(d, *rel.split("/")), PAYLOAD)
    engine = lab.se.ClaudeCodeEngine(model="x")
    work = lab.folder("sandbox")
    os.makedirs(work)
    ran = engine.install_skill(work, d, NAME)
    p, dest = installed(lab, d)
    in_run, in_target = os.path.exists(os.path.join(ran, *rel.split("/"))), os.path.exists(os.path.join(dest, *rel.split("/")))
    return Claim("B2-run-differs-from-install", in_target and not in_run, "a file reaches the install target but not the eval sandbox",
                 ["install_skill of an eval run holds %s: %s" % (rel, in_run), "`install` target holds %s: %s" % (rel, in_target)],
                 "An eval result describes the unit_tree files. The user gets those and more.")


def opens_during(lab, act):
    start = len(drive.audit_rows(lab.audit_path))
    done = act()
    opened = {os.path.basename(r["detail"]) for r in drive.audit_rows(lab.audit_path)[start:] if r["kind"] == "gate-read"}
    return done, sorted(opened & set(RECORDS))


def no_approval(lab):
    bare = lab.skill()
    p, dest = installed(lab, bare)
    ws = os.path.join(os.path.dirname(bare), NAME + "-workspace")
    garbage = lab.skill()
    folder = os.path.dirname(garbage)
    for name in RECORDS: write(os.path.join(folder, NAME + "-workspace", name), b"[not an object")
    (q, _), consulted = opens_during(lab, lambda: installed(lab, garbage))
    _, seen = opens_during(lab, lambda: (lab.tool("eval", "plan", garbage), lab.tool("eval", "drift", folder, "--today", "2026-10-30")))
    if seen != sorted(RECORDS): raise RuntimeError("the hook saw %s opened by plan and drift, so an install that opens none proves nothing" % seen)
    return Claim("B3-project-scope-asks-no-approval", p.returncode == 0 and q.returncode == 0 and not consulted and not os.path.exists(ws),
                 "project-scope install runs with no workspace and with garbage records, and opens neither record",
                 ["no workspace: install exit %d" % p.returncode,
                  "approvals.json and registration.json hold garbage: install exit %d, records the hook saw it open: %s" % (q.returncode, consulted or "none"),
                  "control, plan and drift on the same two records: the hook saw %s opened" % ", ".join(seen)],
                 "Project scope needs no approval, so an unhashed file bypasses no gate there. The cost is that a recorded approval says nothing about what install ships. Only --scope user reads registration_state (skill_eval.py:5703, read, not driven).", False)


def reverse(lab):
    rows, dropped = [], []
    units = [("fixture", os.path.join(lab.root, *drive.FIXTURE))] + [(n, os.path.join(lab.root, "skills", n)) for n in drive.REAL_SKILLS]
    for name, d in units:
        hashed, shipped = set(lab.eva.unit_tree(d).files), {rel for rel, _ in lab.install.skill_files(d)}
        rows.append("%s: %d hashed, %d installed, hashed and not installed: %s" % (name, len(hashed), len(shipped), sorted(hashed - shipped) or "none"))
        dropped += sorted(hashed - shipped)

    def link_file(d): os.symlink("SKILL.md", os.path.join(d, "alias.md"))
    def link_folder(d): os.symlink("scripts", os.path.join(d, "alias"))
    def dangling(d): os.symlink("nowhere", os.path.join(d, "alias"))
    def fifo(d): os.mkfifo(os.path.join(d, "alias"))
    for label, make in (("internal file link", link_file), ("internal folder link", link_folder), ("dangling link", dangling), ("named pipe", fifo)):
        d = lab.skill()
        make(d)
        tree = lab.eva.unit_tree(d)
        hashed = sorted(r for r in tree.files if r.startswith("alias"))
        p, dest = installed(lab, d)
        rows.append("%s: unit_tree lists %s; install exit %d, target folder exists: %s" % (label, hashed, p.returncode, os.path.isdir(dest)))
        if p.returncode == 0 and any(not os.path.lexists(os.path.join(dest, *r.split("/"))) for r in hashed): dropped += hashed
    d = lab.skill()
    os.makedirs(os.path.join(d, "scripts", "empty"))
    p, dest = installed(lab, d)
    rows.append("empty folder: hashed: no; install exit %d, in target: %s" % (p.returncode, os.path.exists(os.path.join(dest, "scripts", "empty"))))
    return Claim("B4-hashed-not-installed", bool(dropped), "a hashed file that a successful install leaves out", rows,
                 "A link or a special file makes install refuse the whole folder, so install never drops a hashed file in silence. It always copies a regular file.")


def real_skills(lab):
    rows, unbound = [], 0
    for name in drive.REAL_SKILLS:
        d = os.path.join(lab.root, "skills", name)
        shipped = {rel for rel, _ in lab.install.skill_files(d)}
        tree = set(lab.eva.unit_tree(d).files)
        bound = {label[len("files:"):] if label.startswith("files:") else label for label, _ in lab.se.eval_set_files(d)}
        free = sorted(shipped - tree - bound)
        unbound += len(free)
        rows.append("%s: %d installed, %d in skill_tree_hash, %d more in eval_set, %d in neither: %s" % (name, len(shipped), len(tree), len(shipped & bound), len(free), free or "none"))
    return Claim("B6-real-skills-unbound-files", unbound > 0, "a file of a shipped skill installs outside every digest", rows,
                 "Measures the three real skills as they stand. skill_tree_hash covers only SKILL.md in each, and the eval digests bind every other file, so the defect is latent in them today.", False)


def mode_bit(lab):
    d = skill_with_no_empty_digest(lab)
    p = os.path.join(d, "scripts", "clean.py")
    before = digests(lab, d)
    os.chmod(p, 0o644)
    q, dest = installed(lab, d)
    first = stat.S_IMODE(os.stat(os.path.join(dest, "scripts", "clean.py")).st_mode)
    os.chmod(p, 0o755)
    after = digests(lab, d)
    r, dest2 = installed(lab, d)
    second = stat.S_IMODE(os.stat(os.path.join(dest2, "scripts", "clean.py")).st_mode)
    moved = sorted(k for k in before if before[k] != after[k])
    return Claim("B5-mode-bit", not moved and first != second, "the execute bit installs and moves no digest",
                 ["mode 0644 installs as %o, mode 0755 installs as %o" % (first, second), "digests moved: %s" % (", ".join(moved) or "none")],
                 "install copies the mode of each file. No hash reads a mode. This is an attribute, not a file class, so it stays out of the overall verdict.", False)


def run(lab):
    return ([controls(lab)] + [file_class(lab, c, r) for c, r in CLASSES]
            + [scan_control(lab), run_versus_install(lab), no_approval(lab), reverse(lab), real_skills(lab), mode_bit(lab)])


if __name__ == "__main__":
    sys.exit(drive.repro_main("repro-b", __doc__, run))
