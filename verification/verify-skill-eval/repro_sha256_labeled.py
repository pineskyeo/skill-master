#!/usr/bin/env python3
"""Candidate A. sha256_labeled separates a label from its content with one NUL byte and frames a marker like content.
Prints one verdict line per sub-claim and one overall line, then exits 0. A crash exits 2 and prints neither
verdict word. Standard library only."""
import sys
sys.dont_write_bytecode = True
import json, os, re, shutil
import drive
from drive import Claim, write_bytes as write

DOCS_AGENT = ("skills", "agent-scenarios", "evals", "files", "doc-linker")
NAME = "csv-cleaner-en"


def spec(d):
    return {"skill_dir": d, "skill_name": NAME}


def workspace(d):
    return os.path.join(os.path.dirname(d), NAME + "-workspace")


def eval_set(lab, d):
    return lab.se.gate_hash("eval_set", spec(d), workspace(d))


def files_of(d):
    out = []
    for dp, dn, fn in os.walk(d):
        dn[:] = sorted(x for x in dn if x != "evals")
        for n in sorted(fn):
            p = os.path.join(dp, n)
            out.append("%s=%s" % (os.path.relpath(p, d), "link->" + os.readlink(p) if os.path.islink(p) else "%d bytes" % os.path.getsize(p)))
    return out


def set_files(d, refs):
    p = os.path.join(d, "evals", "evals.json")
    with open(p, encoding="utf-8") as f: doc = json.load(f)
    doc["evals"][0]["files"] = refs
    write(p, json.dumps(doc).encode("utf-8"))


def line_with(p, pattern):
    return next((x.strip()[:170] for x in (p.stdout + p.stderr).splitlines() if re.search(pattern, x)), "no such line")


def waiver_record(lab, d):
    se, ws = lab.se, workspace(d)
    os.makedirs(ws, exist_ok=True)
    gate = "coverage_waiver:hermes"
    rec = {"format": 1, "method": "recorded", "kind": "approve", "gate": gate, "unit": NAME, "sha256": se.gate_hash(gate, spec(d), ws),
           "ruleset_sha256": se.ruleset_sha256(), "recorded_by": "verify-fixture", "at": "2026-10-30T00:00:00Z",
           "reason": "scratch fixture of the repro"}
    with open(os.path.join(ws, "approvals.json"), "w", encoding="utf-8") as f: json.dump({gate: rec}, f)


def waiver_state(lab, d):
    p = lab.tool("eval", "plan", d)
    m = re.search(r"; waivers: .*hermes=(.+)$", p.stdout, re.M)
    return m.group(1).strip() if m else "plan exit %d, no waiver line" % p.returncode


def a1_split(lab):
    one, two = lab.skill(), lab.skill()
    write(os.path.join(one, "a"), b"x\0b\0y")
    write(os.path.join(two, "a"), b"x")
    write(os.path.join(two, "b"), b"y")
    h1, h2 = lab.se.skill_tree_hash(one), lab.se.skill_tree_hash(two)
    return Claim("A1-split", h1 == h2, "one file a=x\\0b\\0y and two files a=x, b=y through skill_tree_hash",
                 ["one file: %r" % files_of(one), "  a bytes %r" % b"x\0b\0y", "two files: %r" % files_of(two),
                  "  a bytes %r, b bytes %r" % (b"x", b"y"), "digest one %s" % h1, "digest two %s" % h2, lab.verdict_of(one), lab.verdict_of(two)],
                 "Labels come from os.walk. A folder writer needs only a file that may hold a NUL, and eva.py check gives PASS to both folders.")


def a1b_same_names(lab):
    one, two = lab.skill(), lab.skill()
    write(os.path.join(one, "a"), b"x")
    write(os.path.join(one, "b"), b"p\0b\0q")
    write(os.path.join(two, "a"), b"x\0b\0p")
    write(os.path.join(two, "b"), b"q")
    h1, h2 = lab.se.skill_tree_hash(one), lab.se.skill_tree_hash(two)
    return Claim("A1b-same-names", h1 == h2, "the same two file names with other bytes in each file through skill_tree_hash",
                 ["left a=%r b=%r" % (b"x", b"p\0b\0q"), "right a=%r b=%r" % (b"x\0b\0p", b"q"), "digest left %s" % h1, "digest right %s" % h2],
                 "No file appears or disappears, so a comparison of file names shows nothing.")


def a1c_waiver_survives(lab):
    d = lab.skill()
    write(os.path.join(d, "a"), b"x\0b\0y")
    waiver_record(lab, d)
    before = waiver_state(lab, d)
    write(os.path.join(d, "a"), b"x")
    write(os.path.join(d, "b"), b"y")
    after = waiver_state(lab, d)
    write(os.path.join(d, "b"), b"z")
    control = waiver_state(lab, d)
    held = "approved by" in before and "approved by" in after and "approved by" not in control
    return Claim("A1c-approval-survives", held, "a recorded coverage waiver reads approved after the split, through `plan`",
                 ["hand-written record in scratch, read by the real `skill_eval.py plan`", "before the split: %s" % before,
                  "after the split: %s" % after, "control, b edited to z: %s" % control],
                 "A waiver binds skill_tree_hash. registration_state and live_plan_hash read the same digest (skill_eval.py:4037, 6243), not driven here.")


def a2_missing_tree(lab):
    regular, dangling, absent = lab.skill(), lab.skill(), lab.skill()
    write(os.path.join(regular, "zz"), b"<missing>")
    os.symlink("nowhere", os.path.join(dangling, "zz"))
    target = lab.folder("target")
    os.makedirs(target)
    install = lab.tool("eval", "install", dangling, "--target", target, "--tools", "claude")
    snap = lab.tool("eval", "snapshot", dangling)
    h = lab.se.skill_tree_hash
    return (Claim("A2-missing-dangling-link", h(regular) == h(dangling), "a regular file holding <missing> and a dangling link through skill_tree_hash",
                  ["regular zz=%r digest %s" % (b"<missing>", h(regular)), "link zz->nowhere digest %s" % h(dangling), "regular: " + lab.verdict_of(regular),
                   "dangling: " + lab.verdict_of(dangling), "install exit %d: %s" % (install.returncode, line_with(install, "symbolic link")),
                   "snapshot exit %d: %s" % (snap.returncode, line_with(snap, "dangling|link"))],
                  "unit_tree lists a dangling link inside the unit under files, so it reaches the <missing> branch. eva.py check does not flag it. install and snapshot refuse it."),
            Claim("A2b-missing-absent-tree", h(regular) == h(absent), "a regular file holding <missing> and an absent file through skill_tree_hash",
                  ["with zz digest %s" % h(regular), "without zz digest %s" % h(absent)],
                  "An absent file has no pair, so skill_tree_hash hashes neither its label nor the marker."))


def a2c_missing_eval_set(lab):
    held, gone = lab.skill(), lab.skill()
    fixture = os.path.join("evals", "files", "orders_2025.csv")
    write(os.path.join(held, fixture), b"<missing>")
    os.unlink(os.path.join(gone, fixture))
    trig_held, trig_gone = lab.skill(), lab.skill()
    write(os.path.join(trig_held, "evals", "trigger_evals.json"), b"<missing>")
    os.unlink(os.path.join(trig_gone, "evals", "trigger_evals.json"))
    a, b, c, d = eval_set(lab, held), eval_set(lab, gone), eval_set(lab, trig_held), eval_set(lab, trig_gone)
    va, vb = lab.tool("eval", "validate", held), lab.tool("eval", "validate", gone)
    return Claim("A2c-missing-eval-set", a == b and c == d, "a listed file holding <missing> and an absent one through the eval_set gate hash",
                 ["listed fixture holds <missing>: %s" % a, "listed fixture absent:        %s" % b, "trigger_evals.json holds <missing>: %s" % c,
                  "trigger_evals.json absent:          %s" % d, "validate, fixture holds <missing>: exit %d" % va.returncode,
                  "validate, fixture absent: exit %d: %s" % (vb.returncode, line_with(vb, "not found"))],
                 "eval_set_files always lists its paths, so an absent file reaches the <missing> branch. `validate` refuses the absent state, but the digest alone does not tell the two states apart.")


def a3_outside(lab):
    target = os.path.join(lab.folder("outside"), "secret.txt")
    write(target, b"any bytes outside the folder")
    regular, linked, early_reg, early_link = lab.skill(), lab.skill(), lab.skill(), lab.skill()
    write(os.path.join(regular, "scripts", "zz"), b"<outside>")
    os.symlink(target, os.path.join(linked, "scripts", "zz"))
    write(os.path.join(early_reg, "zz"), b"<outside>")
    os.symlink(target, os.path.join(early_link, "zz"))
    h = lab.se.skill_tree_hash
    h1, h2 = h(regular), h(linked)
    write(target, b"other bytes outside the folder")
    h3 = h(linked)
    return (Claim("A3-outside-last", h1 == h2, "a regular file holding <outside>, last in walk order, and a link out of the folder through skill_tree_hash",
                  ["regular: %r" % files_of(regular), "link: %r" % files_of(linked), "digest regular %s" % h1, "digest link %s" % h2,
                   "digest link after the target changed %s" % h3, "the added content holds no NUL byte", "regular: " + lab.verdict_of(regular), "link: " + lab.verdict_of(linked)],
                  "Pairs hash first, then marked, so the streams match only when the regular file is the last pair of the walk."),
            Claim("A3b-outside-not-last", h(early_reg) == h(early_link), "the same pair with zz at the folder root, before scripts/",
                  ["regular digest %s" % h(early_reg), "link digest %s" % h(early_link)],
                  "The link moves to the end of the stream, so a file that is not last does not collide."))


def a3c_cycle(lab):
    regular, linked = lab.skill(), lab.skill()
    write(os.path.join(regular, "scripts", "zz"), b"<cycle>")
    os.symlink("..", os.path.join(linked, "scripts", "zz"))
    h1, h2 = lab.se.skill_tree_hash(regular), lab.se.skill_tree_hash(linked)
    return Claim("A3c-cycle-last", h1 == h2, "a regular file holding <cycle>, last in walk order, and a folder link up its own path",
                 ["regular digest %s" % h1, "link digest %s" % h2], "The framing is the same as for <outside>.")


def a3d_gates(lab):
    target = os.path.join(lab.folder("outside"), "payload.sh")
    write(target, b"echo changed\n")
    d = lab.skill()
    write(os.path.join(d, "scripts", "zz"), b"<outside>")
    waiver_record(lab, d)
    before = waiver_state(lab, d)
    os.unlink(os.path.join(d, "scripts", "zz"))
    os.symlink(target, os.path.join(d, "scripts", "zz"))
    after = waiver_state(lab, d)
    check = lab.tool("eva", "check", d)
    dest = lab.folder("target")
    os.makedirs(dest)
    install, snap = lab.tool("eval", "install", d, "--target", dest, "--tools", "claude"), lab.tool("eval", "snapshot", d)
    refusals = (("eva.py check", check, r"\(link\)"), ("install", install, "symbolic link"), ("snapshot", snap, r"\(link\)"))
    refused = all(p.returncode != 0 and re.search(rx, p.stdout + p.stderr) for _, p, rx in refusals)
    return Claim("A3d-link-approval-survives", "approved by" in before and "approved by" in after and refused,
                 "a recorded waiver reads approved after a regular file becomes a link out of the folder, and check, install and snapshot refuse the link",
                 ["before: %s" % before, "after: %s" % after] + ["%s exit %d: %s" % (name, p.returncode, line_with(p, rx)) for name, p, rx in refusals],
                 "The digest survives, but check, install and snapshot each refuse the link on their own walk. The swap fools a stored record, not a copy.")


def a4_nul_path(lab):
    se, d = lab.se, lab.skill()
    set_files(d, ["evals/files/orders_2025.csv\u0000ghost"])
    pairs = se.eval_set_files(d)
    nul = next(((label, p) for label, p in pairs if "\0" in label), None)
    if nul is None: return Claim("A4-nul-path-hashes-missing", False, "a JSON files entry with \\u0000 reaches a label", ["no label holds a NUL"], "not reachable", False)
    h, missing = se.sha256_labeled([nul]), se.sha256_labeled([(nul[0], os.path.join(d, "no-such-file"))])
    valid, check, plan = lab.tool("eval", "validate", d), lab.tool("eva", "check", d), lab.tool("eval", "plan", d)
    pending = lab.tool("eval", "pending", "--unit", d)
    return Claim("A4-nul-path-hashes-missing", h == missing, "a JSON files entry with \\u0000 reaches the label and hashes as <missing>",
                 ["label %r" % nul[0], "os.path.isfile on the path: %s" % os.path.isfile(nul[1]), "digest of the NUL pair %s" % h,
                  "digest of the same label on an absent path %s" % missing, "eval_set gate hash, no exception: %s" % eval_set(lab, d),
                  "validate exit %d: %s" % (valid.returncode, line_with(valid, "evals.json")), "eva.py check exit %d: %s" % (check.returncode, line_with(check, "evals-valid")),
                  "plan exit %d: %s" % (plan.returncode, line_with(plan, "eval-set-valid")), "pending exit %d: %s" % (pending.returncode, line_with(pending, "pending approval"))],
                 "The real entry points do not crash, and validate and the eval-set-valid live gate refuse the entry. See A4c for why no collision follows.", False)


def a4b_nul_collision(lab):
    se = lab.se
    pair = os.path.join(lab.folder("raw"), "a")
    write(pair, b"b\0<missing>")
    raw_one, raw_two = se.sha256_labeled([("a", pair)]), se.sha256_labeled([("a\0b", os.path.join(lab.base, "absent"))])
    left, right = lab.skill(), lab.skill()
    set_files(left, ["evals/files/zz"])
    set_files(right, ["evals/files/zz\u0000b"])
    write(os.path.join(left, "evals", "files", "zz"), b"b\0<missing>")
    return (Claim("A4b-nul-label-raw", raw_one == raw_two, "a label with a NUL collides with a shorter label on the raw function",
                  ["sha256_labeled([('a', file holding %r)]) = %s" % (b"b\0<missing>", raw_one), "sha256_labeled([('a\\0b', absent)]) = %s" % raw_two],
                  "It needs a caller that passes a label with a NUL. A4 reaches the hash, and A8 stops at the label."),
            Claim("A4c-nul-label-gate", eval_set(lab, left) == eval_set(lab, right), "the same two states collide through the eval_set gate hash",
                  ["left %s" % eval_set(lab, left), "right %s" % eval_set(lab, right)],
                  "evals.json holds the files list and is hashed first, so the two states differ before the framing matters."))


def a5_eval_set_swap(lab):
    left, right = lab.skill(), lab.skill()
    listed = [label for label, _ in lab.se.eval_set_files(left) if label.startswith("files:")]
    first, second = listed[-2], listed[-1]
    tag = second.encode()
    for d, a, b in ((left, b"x", b"p\0" + tag + b"\0q"), (right, b"x\0" + tag + b"\0p", b"q")):
        write(os.path.join(d, *first[len("files:"):].split("/")), a)
        write(os.path.join(d, *second[len("files:"):].split("/")), b)
    return Claim("A5-eval-set-swap", eval_set(lab, left) == eval_set(lab, right), "two listed fixtures change bytes under one eval_set gate hash",
                 ["adjacent labels %r, %r" % (first, second), "left %s" % eval_set(lab, left), "right %s" % eval_set(lab, right)],
                 "A CSV fixture may hold a NUL. evals.json stays byte-identical, so no earlier pair changes.")


def a6_agent_fixture(lab):
    left, right = lab.folder("agents", "doc-linker"), lab.folder("agents", "doc-linker")
    for d in (left, right):
        shutil.copytree(os.path.join(lab.root, *DOCS_AGENT), d)
        shutil.rmtree(os.path.join(d, "fixtures", "docs-basic"))
    write(os.path.join(left, "fixtures", "docs-basic", "a"), b"x\0b\0y")
    write(os.path.join(right, "fixtures", "docs-basic", "a"), b"x")
    write(os.path.join(right, "fixtures", "docs-basic", "b"), b"y")
    shown = [line_with(lab.tool("eval", "agent", "plan", d), r"^fixture one-broken-link") for d in (left, right)]
    digests = [re.search(r"sha256 ([0-9a-f]{64})", x).group(1) for x in shown]
    return Claim("A6-agent-fixture-split", digests[0] == digests[1], "one and two fixture files print one sha256 in `agent plan`", shown,
                 "fixture_record and the plan line hash labels relative to the fixture folder, from os.walk. plan.json and run.json record this digest.")


def a7_ruleset(lab):
    left, right = lab.folder("ruleset"), lab.folder("ruleset")
    for d in (left, right):
        os.makedirs(d)
        for sub in ("rules", "schemas", "targets", "policy"): shutil.copytree(os.path.join(lab.root, sub), os.path.join(d, sub))
    write(os.path.join(left, "rules", "data", "zz-a"), b"x\0rules/data/zz-b\0y")
    write(os.path.join(right, "rules", "data", "zz-a"), b"x")
    write(os.path.join(right, "rules", "data", "zz-b"), b"y")
    a, b = lab.rr.load_bundle(root=left).sha256, lab.rr.load_bundle(root=right).sha256
    return Claim("A7-ruleset-split", a == b, "one and two data files through engine/rule_runner.load_bundle", ["one file %s" % a, "two files %s" % b],
                 "Labels come from os.walk. It needs write access to rules/.")


def a8_census(lab):
    se, lines = lab.se, []
    try:
        os.close(os.open(os.path.join(lab.base, "a\0b"), os.O_WRONLY | os.O_CREAT))
        lines.append("os.open of a name with a NUL: created")
    except ValueError as ex:
        lines.append("os.open of a name with a NUL: ValueError %s, so an os.walk name cannot hold one" % ex)
    agent = lab.folder("agents", "doc-linker")
    shutil.copytree(os.path.join(lab.root, *DOCS_AGENT), agent)
    md = os.path.join(agent, "AGENT.md")
    with open(md, encoding="utf-8") as f: text = f.read()
    write(md, text.replace("name: doc-linker", 'name: "doc\\0linker"', 1).encode("utf-8"))
    a = se.Agent(agent)
    lines.append("AGENT.md name with a YAML escape: agent.name %r, frontmatter valid %s" % (a.name, a.meta is not None))
    write(md, text.replace("name: doc-linker", "name: doc\0linker", 1).encode("utf-8"))
    a = se.Agent(agent)
    lines.append("AGENT.md name with a raw NUL: agent.name %r, frontmatter valid %s" % (a.name, a.meta is not None))
    labels = [label for label, _ in a.files(["claude"]) if "\0" in label]
    lines.append("agent.files(['claude']) NUL labels: %r" % labels)
    plain = lab.tool("eval", "agent", "plan", agent)
    named = lab.tool("eval", "agent", "plan", agent, "--workspace", os.path.join(lab.base, "ws-agent"))
    check = lab.tool("eva", "check", agent)
    lines += ["agent plan exit %d: %s" % (plain.returncode, line_with(plain, "safe path component")),
              "agent plan --workspace exit %d: %s" % (named.returncode, line_with(named, "^cell ")),
              "eva.py check exit %d: %s" % (check.returncode, line_with(check, "parity-agent-name"))]
    write(md, text.encode("utf-8"))
    scenario = os.path.join(agent, "scenarios", "01-one-broken-link.json")
    with open(scenario, encoding="utf-8") as f: doc = json.load(f)
    doc["fixture"] = "docs\u0000basic"
    write(scenario, json.dumps(doc).encode("utf-8"))
    valid = len(se.Agent(agent).scenarios)
    lines.append("scenario fixture slug with \\u0000: %d valid scenario(s)%s" % (valid, "" if valid else ", so the slug never reaches a label"))
    return Claim("A8-agent-name-nul", bool(labels), "an AGENT.md name carries a NUL into the labels of Agent.files", lines,
                 "Agent.files feeds lines 3882, 4931, 4932, 5130, 5131 and agent_bound_files near 5437, and the census says why none of them was driven. "
                 "This is label-level, not a hash-level collision. AGENT.md is hashed first, so a NUL name moves the digest, as in A4c.", False)


NOT_DRIVEN = "yes at Agent.files, A8; problems(left) at 5013 refuses before this hash, because a NUL name has no current wrapper; not driven"
CENSUS = ("caller census, read from the code at this commit and not computed (site | label source | can the label hold a NUL):",
          "    engine/rule_runner.py:184 load_bundle | ruleset_pairs: os.walk names under rules/, schemas/, targets/, and the constant POLICY_FILES | no",
          "    skill_eval.py:301 ruleset_sha256 | the same ruleset_pairs | no",
          "    skill_eval.py:310 skill_tree_hash | eva.unit_tree: os.walk names | no",
          "    skill_eval.py:2268 gate_hash eval_set, skill | two constants and 'files:' + evals[].files, a JSON string | yes, A4",
          "    skill_eval.py:2268 gate_hash eval_set, agent | os.listdir of scenarios/ and os.walk of fixtures/ | no",
          "    skill_eval.py:2302 rule_review_hash | constants, os.walk names, and a wrapper name kept only when os.path.isfile is True | no",
          "    skill_eval.py:3882 registration_record | Agent.files: walk names and wrapper_rel(tool, AGENT.md name) | yes at Agent.files, A8; the site needs a benchmark only a run writes; not driven",
          "    skill_eval.py:4541 fixture_record | fixture_files: os.walk names relative to the fixture | no",
          "    skill_eval.py:4552 agent_input_lines | the same fixture_files | no",
          "    skill_eval.py:4931 agent_run_iteration agent_sha256 | Agent.files | " + NOT_DRIVEN,
          "    skill_eval.py:4932 agent_run_iteration old_agent_sha256 | Agent.files of --old-agent | " + NOT_DRIVEN,
          "    skill_eval.py:5130 agent_run parts | Agent.files | " + NOT_DRIVEN,
          "    skill_eval.py:5131 agent_run parts old_agent | Agent.files of --old-agent | " + NOT_DRIVEN,
          "    skill_eval.py:5186 agent_grade shas | Agent.files([]): no wrapper name | no",
          "    skill_eval.py:5437 agent_bound_files | Agent.files, with its own copy of the framing, not a call | "
          "yes at Agent.files, A8; its callers 5537-5538 sit behind the same refusal; not driven")


def run(lab):
    claims = []
    for fn in (a1_split, a1b_same_names, a1c_waiver_survives, a2_missing_tree, a2c_missing_eval_set, a3_outside, a3c_cycle, a3d_gates,
               a4_nul_path, a4b_nul_collision, a5_eval_set_swap, a6_agent_fixture, a7_ruleset, a8_census):
        got = fn(lab)
        claims += [got] if isinstance(got, Claim) else list(got)
    return claims


if __name__ == "__main__":
    sys.exit(drive.repro_main("repro-a", __doc__, run, CENSUS))
