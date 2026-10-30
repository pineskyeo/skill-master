import collections, json, os, shutil, subprocess, sys, time

Mutant = collections.namedtuple("Mutant", "id path old new kills base", defaults=(None,))
Fix = collections.namedtuple("Fix", "id edits holds")

OLD_LABELED = r'''    h = hashlib.sha256()
    for label, p in pairs:
        h.update(label.encode() + b"\0")
        if os.path.isfile(p):
            with open(p, "rb") as f: h.update(f.read())
        else:
            h.update(b"<missing>")
        h.update(b"\0")
    for label, marker in marked:
        h.update(label.encode() + b"\0" + marker + b"\0")
    return h.hexdigest()
'''
NEW_LABELED = r'''    h = hashlib.sha256()
    def field(b): h.update(b"%d:" % len(b) + b)
    for label, p in pairs:
        field(label.encode())
        if os.path.isfile(p):
            field(b"file")
            with open(p, "rb") as f: field(f.read())
        else:
            field(b"missing")
    for label, marker in marked:
        field(label.encode()); field(b"marked"); field(marker)
    return h.hexdigest()
'''
OLD_BOUND = r'''    h, each = hashlib.sha256(), {}
    for label, p in pairs:
        h.update(label.encode() + b"\0")
        if os.path.isfile(p):
            with open(p, "rb") as f: data = f.read()
            h.update(data)
            each[label] = hashlib.sha256(data).hexdigest()
        else:
            h.update(b"<missing>")
            each[label] = None
        h.update(b"\0")
    return h.hexdigest(), each
'''
NEW_BOUND = r'''    h, each = hashlib.sha256(), {}
    def field(b): h.update(b"%d:" % len(b) + b)
    for label, p in pairs:
        field(label.encode())
        if os.path.isfile(p):
            field(b"file")
            with open(p, "rb") as f: data = f.read()
            field(data)
            each[label] = hashlib.sha256(data).hexdigest()
        else:
            field(b"missing")
            each[label] = None
    return h.hexdigest(), each
'''
OLD_INSTALL = "    return sorted(out)\n\n\ndef declared_name"
NEW_INSTALL = '''    import fnmatch
    ignored = ("evals", "__pycache__", "*-workspace", ".git", "FEEDBACK.md", "FEEDBACK.json")
    return sorted(x for x in out if not any(fnmatch.fnmatch(part, pat) for part in x[0].split("/") for pat in ignored))


def declared_name'''

SPEND, SE, RR, INSTALL = "engine/spend.py", "skill_eval.py", "engine/rule_runner.py", "engine/install.py"

FIXES = (Fix("fix-framing", ((SE, OLD_LABELED, NEW_LABELED), (RR, OLD_LABELED, NEW_LABELED), (SE, OLD_BOUND, NEW_BOUND)),
             ("P-framing", "P-framing-copies")),
         Fix("fix-install", ((INSTALL, OLD_INSTALL, NEW_INSTALL),), ("P-coverage",)))

MUTANTS = (
    Mutant("lock-gone", SPEND, "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "pass", "L-exclusive"),
    Mutant("lock-shared", SPEND, "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)",
           "L-exclusive"),
    Mutant("preview-unchecked", SPEND, 'if sorted(r["call_id"] for r in now) != sorted(expected):', "if False:",
           "L-preview-matches"),
    Mutant("recovered-forgotten", SPEND, ' | {r["recovers"] for r in rows if r["type"] == "recovered"}', "", "L-counted-once"),
    Mutant("recovery-skipped", SPEND, "            self._append(row)\n        self.recovered_usd", "            pass\n        self.recovered_usd",
           "L-counted-once"),
    Mutant("intent-dropped", SPEND, "        self.ledger._append(self.intent_row())\n        self.launched = True",
           "        self.launched = True", "L-intent-first"),
    Mutant("cap-unchecked", SPEND, "if reason is None and round(spent + bound, 6) > round(self.cap, 6):", "if False:", "L-hard-cap"),
    Mutant("recovered-free", SPEND, "self.recovered_usd = usd(total)", "self.recovered_usd = usd(0.0)", "L-hard-cap"),
    Mutant("torn-kept", SPEND, "            if self.torn:\n                os.ftruncate(fd, keep); os.fsync(fd)\n", "", "L-readable"),
    Mutant("copy-drifts", SE, '            h.update(b"<missing>")\n            each[label] = None', '            h.update(b"<absent>")\n            each[label] = None',
           "P-framing-copies"),
    Mutant("fix-missing-as-content", SE, '            field(b"missing")\n    for label, marker in marked:',
           '            field(b"file"); field(b"<missing>")\n    for label, marker in marked:', "P-framing", "fix-framing"),
    Mutant("fix-marked-as-content", SE, 'field(label.encode()); field(b"marked"); field(marker)',
           'field(label.encode()); field(b"file"); field(marker)', "P-framing", "fix-framing"),
    Mutant("fix-leaves-third-copy", SE, NEW_BOUND, OLD_BOUND, "P-framing-copies", "fix-framing"),
    Mutant("fix-ships-feedback", INSTALL, '".git", "FEEDBACK.md", "FEEDBACK.json")', '".git", "FEEDBACK.json")', "P-coverage", "fix-install"),
    Mutant("fix-ships-pycache", INSTALL, '("evals", "__pycache__", "*-workspace"', '("evals", "*-workspace"', "P-coverage", "fix-install"),
)
CAP_UNCHECKED = next(m for m in MUTANTS if m.id == "cap-unchecked")
GUARD_OVERBIDS = Mutant("guard-overbids", SE, "cap = budget.claude_cap()\n        if cap < 0.01:\n            return None, 0.0, \"budget exhausted",
                        "cap = budget.claude_cap() * 4\n        if cap < 0.01:\n            return None, 0.0, \"budget exhausted", "L-hard-cap")
LAUNCH_RAISES = Mutant("launch-raises", SPEND, 'if self.decision != "allowed" or self.launched:', "if True:", "L-hard-cap")
WRITES_INTO_TREE = Mutant("writes-into-tree", RR, OLD_LABELED, '    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "stray"), "w").close()\n' + OLD_LABELED,
                          "P-framing-copies")


class EditError(Exception):
    pass


def apply_edit(root, rel, old, new, label):
    path = os.path.join(root, *rel.split("/"))
    with open(path, encoding="utf-8", newline="") as f: text = f.read()
    if text.count(old) != 1:
        raise EditError("%s: its old text matches %s %d times, not once, so the code changed and this edit needs another look"
                        % (label, rel, text.count(old)))
    with open(path, "w", encoding="utf-8", newline="") as f: f.write(text.replace(old, new))


def run_child(script, root, pid, *more):
    p = subprocess.run([sys.executable, "-B", script, "--root", root, "--only", pid, "--stop-first", "--json"] + list(more),
                       capture_output=True, encoding="utf-8", timeout=280, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if p.returncode != 0 or not p.stdout.strip():
        raise EditError("the check crashed on %s (exit %d): %s" % (pid, p.returncode, p.stderr.strip()[-400:]))
    return json.loads(p.stdout.strip().splitlines()[-1])[pid]


def plain_run(script, root, pid="L-exclusive"):
    only = ["--only", pid, "--stop-first"] if pid else []
    return subprocess.run([sys.executable, "-B", script, "--root", root] + only, capture_output=True,
                          encoding="utf-8", timeout=280, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


def snapshot(root, rels):
    saved = {}
    for rel in rels:
        with open(os.path.join(root, *rel.split("/")), "rb") as f: saved[rel] = f.read()
    return saved


def restore(root, saved):
    for rel, data in saved.items():
        with open(os.path.join(root, *rel.split("/")), "wb") as f: f.write(data)


def refuses(root, rel, old):
    try:
        apply_edit(root, rel, old, "", "self-test")
    except EditError:
        return True
    return False


def run(root, script):
    import check
    started = time.monotonic()
    before = check.tree_digest(root)
    copy = check.scratch("mutants")
    code = os.path.join(copy, "code")
    fails, lines = [], []

    def say(ok, text):
        line = "%s %s" % ("ok  " if ok else "FAIL", text)
        print(line, flush=True)
        lines.append(line)
        if not ok: fails.append(text)

    try:
        shutil.copytree(root, code, ignore=shutil.ignore_patterns(".git", "verification", "__pycache__"))
        saved = snapshot(code, (SE, RR, SPEND, INSTALL))
        fixes = {f.id: f for f in FIXES}
        say(refuses(code, SPEND, "text no version of this file holds"), "an old text with 0 matches stops the run")
        say(refuses(code, SPEND, "def "), "an old text with many matches stops the run")
        was = check.tree_digest(code)
        check.write(os.path.join(code, "extra"), b"x")
        say(check.tree_digest(code) != was, "the code digest moves when a file appears in the scratch copy")
        os.unlink(os.path.join(code, "extra"))
        for fix in FIXES:
            for i, (rel, old, new) in enumerate(fix.edits): apply_edit(code, rel, old, new, "%s edit %d" % (fix.id, i + 1))
            for pid in fix.holds:
                r = run_child(script, code, pid)
                say(r["status"] == check.HOLDS, "%s: %s reads %s on the fixed copy (%d cases)" % (fix.id, pid, r["status"], r["cases"]))
            restore(code, saved)
        for m in MUTANTS:
            if m.base:
                for i, (rel, old, new) in enumerate(fixes[m.base].edits): apply_edit(code, rel, old, new, "%s edit %d" % (m.base, i + 1))
            apply_edit(code, m.path, m.old, m.new, m.id)
            r = run_child(script, code, m.kills)
            say(r["status"] == check.BROKEN, "%s: %s reads %s%s (%d cases)" % (m.id, m.kills, r["status"],
                                                                                " after %s" % m.base if m.base else "", r["cases"]))
            for x in (r["counterexample"] or [])[:2]: print("     %s" % x[:170], flush=True)
            restore(code, saved)
        offers = check.ledger_search.real_offers(check.load(code).se)
        say(offers == [0.02, 0.01, 0.01], "guard_call offers %s from $0.05, then refuses" % offers)
        apply_edit(code, CAP_UNCHECKED.path, CAP_UNCHECKED.old, CAP_UNCHECKED.new, CAP_UNCHECKED.id)
        r = run_child(script, code, CAP_UNCHECKED.kills, "--real-order")
        say(r["status"] == check.HOLDS, "cap-unchecked: %s reads %s on the real-order configurations, where guard_call offers half "
            "of what is left, so Ledger.admit is a second line (%d cases)" % (CAP_UNCHECKED.kills, r["status"], r["cases"]))
        apply_edit(code, GUARD_OVERBIDS.path, GUARD_OVERBIDS.old, GUARD_OVERBIDS.new, GUARD_OVERBIDS.id)
        r = run_child(script, code, GUARD_OVERBIDS.kills, "--real-order")
        say(r["status"] == check.BROKEN, "cap-unchecked and guard-overbids: %s reads %s on the real-order configurations (%d cases)"
            % (GUARD_OVERBIDS.kills, r["status"], r["cases"]))
        for x in (r["counterexample"] or [])[:2]: print("     %s" % x[:170], flush=True)
        restore(code, saved)
        lock = MUTANTS[0]
        apply_edit(code, lock.path, lock.old, lock.new, lock.id)
        p = plain_run(script, code)
        say(p.returncode == 1 and "VERIFIED CODE CHANGED: engine/spend.py:*" in p.stdout,
            "the plain command on a broken copy exits %d and prints the pin line" % p.returncode)
        say("VERIFIED CODE MOVED: model action TryLock" in p.stdout, "the same run prints the moved-action line for TryLock")
        restore(code, saved)
        apply_edit(code, WRITES_INTO_TREE.path, WRITES_INTO_TREE.old, WRITES_INTO_TREE.new, WRITES_INTO_TREE.id)
        p = plain_run(script, code, WRITES_INTO_TREE.kills)
        say(p.returncode == 2 and "changed during the check" in p.stderr,
            "a check that writes into the code under test exits %d and names the changed tree" % p.returncode)
        os.unlink(os.path.join(code, "engine", "stray"))
        restore(code, saved)
        apply_edit(code, LAUNCH_RAISES.path, LAUNCH_RAISES.old, LAUNCH_RAISES.new, LAUNCH_RAISES.id)
        p = plain_run(script, code, LAUNCH_RAISES.kills)
        say(p.returncode == 2 and "was not admitted" in p.stderr,
            "a SpendError other than the two open refusals fails the search: exit %d" % p.returncode)
        restore(code, saved)
        gates = os.path.join(copy, "gates")
        shutil.copytree(check.HERE, gates)
        apply_edit(gates, "SpendLedger.tla", "\\/ CutTorn(c)", "\\/ CutTail(c)", "action-renamed")
        p = plain_run(os.path.join(gates, "check.py"), code, None)
        say(p.returncode == 2 and "name different actions" in p.stderr,
            "the whole command with one action renamed in a scratch copy of the .tla exits %d and names the drift" % p.returncode)
        empty = os.path.join(copy, "empty")
        os.makedirs(empty)
        p = plain_run(script, empty)
        say(p.returncode == 2, "the plain command on a folder with no code exits %d" % p.returncode)
    except EditError as ex:
        print("checker error: %s" % ex, file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(copy, True)
    if check.tree_digest(root) != before:
        print("checker error: the clone changed during the mutant run", file=sys.stderr)
        return 2
    lines.append("mutants: %d checks, %d failed, clone unchanged, %.1fs" % (len(lines), len(fails), time.monotonic() - started))
    print(lines[-1])
    print("evidence %s" % check.save_evidence("mutants", lines))
    return 1 if fails else 0
