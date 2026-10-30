#!/usr/bin/env python3
"""Bounded checks of the promise that a recorded approval binds exactly what it approved, and of the spend ledger.
Prints each property with its status, bound, case count and counterexample. Exit 0 when every property matches
BASELINE, 1 when one differs, 2 when the checker cannot run. With --json a differing property still exits 0, because
mutants.py reads the status from that line and takes any other exit as a crash. Python 3.9 standard library only."""
import sys
sys.dont_write_bytecode = True
import argparse, ast, collections, hashlib, itertools, json, os, shutil, tempfile, time, traceback, types
import ledger_search

HERE = os.path.dirname(os.path.abspath(__file__))
CLONE = os.path.dirname(os.path.dirname(HERE))
BASE = os.path.join(os.path.realpath(tempfile.gettempdir()), "formal-gates")
SCRATCH, EVIDENCE = os.path.join(BASE, "scratch"), os.path.join(BASE, "evidence")
FIXTURE = ("tests", "fixtures", "skills", "csv-cleaner-en")
NAME = "csv-cleaner-en"
HOLDS, BROKEN = "HOLDS", "BROKEN"

Property = collections.namedtuple("Property", "id sentence harm code bound check")
Result = collections.namedtuple("Result", "status cases counterexample")
Entry = collections.namedtuple("Entry", "kind label data")

BASELINE = {"P-framing": BROKEN, "P-framing-copies": HOLDS, "P-coverage": BROKEN, "L-exclusive": HOLDS,
            "L-preview-matches": HOLDS, "L-counted-once": HOLDS, "L-intent-first": HOLDS, "L-hard-cap": HOLDS,
            "L-readable": HOLDS}

PINS = {
    "engine/install.py:skill_files": "76c5f5aec1acd5a52980a72ca42a429e1ffb1ca1d21b7e717d391798dff85894",
    "engine/primitives/script_denylist.py:SKILL_IGNORE": "557773a05fafd46ecbef8a5ee823cabda46113ca496ffce8c4afeec3859f61e8",
    "engine/primitives/script_denylist.py:unit_tree": "e47e9f79e7cc631a81cc74d9f55a9d5db9c2e2ce9d578a8cbd7565defc22ec39",
    "engine/rule_runner.py:sha256_labeled": "affa72ff92ea4987b8e09ec49dd8f916b9831d085ee194a5e9f7515ecad15b4d",
    "engine/spend.py:*": "2cb76318e335eda3d34daa08d00111a410922d813c4f424c3297269bcf6e3c3c",
    "skill_eval.py:Budget": "26c7a54d2bc0455537ee0fcd288f9a0eef276dc24d62a52e27e30645aa240afd",
    "skill_eval.py:_dispatch": "6732020545a33d97259c14aff1132fa4f1121be1ba8449cafe5fbddd7f78d589",
    "skill_eval.py:_live_estimate": "2fef97730dcd68e23fb3448365aa6159f60bc94359f8ef27452f16d858a802b4",
    "skill_eval.py:agent_bound_files": "f46247f0e7d5437ae85b7fd07e519195ca7bfd7a2d15952d82f47af4ac73e8d0",
    "skill_eval.py:agent_live_start": "2dc1c8300d791332ecb8733c1005e4bb224bd65ec61e93ce55bbed8520e3e42b",
    "skill_eval.py:eval_set_files": "05af5c7bf96ece6f60dddd493c4ab0f68f9c22e562c130269993579ec0150f97",
    "skill_eval.py:gate_hash": "e2a71d016dce2afb042a457b8012a7dcddee6655428cd5dc2b464f5d09bbe2f3",
    "skill_eval.py:guard_call": "5325f4865a7ca978dfe8e13b39190777c41d6e808f7c52b34157e8cd6627ad6b",
    "skill_eval.py:live_exec": "96ec096280d88d29627f56ea44ee18052581917683029368939dc1f595145530",
    "skill_eval.py:live_plan_hash": "a5b490d57f24f1c052bca1cf69092f3b1bc37f78f43a233e66eded809c86c206",
    "skill_eval.py:run_iteration": "3065cbd7f9ac62aa6356c0162f4cad5019bb8c5ebe7d7c107ea048d9952eef7b",
    "skill_eval.py:sha256_labeled": "affa72ff92ea4987b8e09ec49dd8f916b9831d085ee194a5e9f7515ecad15b4d",
    "skill_eval.py:skill_tree_hash": "9d060fad9c5690f279ab15867ba26bbcac0876fd7f28bca0e958fc938966df54",
    "skill_eval.py:spend_admit": "a2ab601b74011a0b4dd416c139a84ec838e9a3ec40738a2c7c5c7cf71f46ce9e",
    "skill_eval.py:spend_settle": "9f2317a43f48902c8baee175f0742aa392b253e5ab77b1b45adfe94793344975",
    "skill_eval.py:waiver_hash": "88a5b9150860563ff61b0ba83367d0fd306a613e1eac0b4ef0629d2c68f7e2e9",
}


class CheckerError(Exception):
    pass


def load(root):
    if root not in sys.path: sys.path.insert(0, root)
    import eva, skill_eval
    from engine import install, rule_runner, spend
    if os.path.dirname(os.path.realpath(skill_eval.__file__)) != root:
        raise CheckerError("imported %s, not the code under %s" % (skill_eval.__file__, root))
    return types.SimpleNamespace(eva=eva, se=skill_eval, install=install, rr=rule_runner, spend=spend)


def scratch(name):
    os.makedirs(SCRATCH, exist_ok=True)
    return tempfile.mkdtemp(prefix=name + "-", dir=SCRATCH)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(data)


def tree_digest(root):
    h = hashlib.sha256()
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d != ".git")
        for n in sorted(fn):
            p = os.path.join(dp, n)
            with open(p, "rb") as f: h.update(os.path.relpath(p, root).encode("utf-8") + b"\0" + f.read() + b"\0")
    return h.hexdigest()


MARKERS = (b"<outside>", b"<cycle>")
SENTINELS = (b"<missing>",) + MARKERS
TOKENS = (b"\0", b"a", b"b") + SENTINELS
LABELS = ("a", "b", "a\0b")
TOKENS_FOR_ENTRIES = {0: 0, 1: 5, 2: 4, 3: 3, 4: 1}
FRAMING_BOUND = ("every list of 0 to 4 entries, each a file, an absent path or a marked link, labels a, b and a NUL b, "
                 "contents built from 6 tokens (NUL, a, b and the three sentinels), with at most 5, 4, 3 and 1 tokens in "
                 "all for 1, 2, 3 and 4 entries")


def compositions(total, slots):
    if slots == 0:
        if total == 0: yield ()
        return
    for first in range(total + 1):
        for rest in compositions(total - first, slots - 1): yield (first,) + rest


def framing_entries():
    most = max(TOKENS_FOR_ENTRIES.values()) + 1
    bodies = {k: sorted({b"".join(x) for x in itertools.product(TOKENS, repeat=k)}) for k in range(most)}
    pairs = {k: [Entry("file", label, body) for label in LABELS for body in bodies[k]] for k in range(most)}
    pairs[0] += [Entry("absent", label, None) for label in LABELS]
    return pairs, [Entry("marked", label, marker) for label in LABELS for marker in MARKERS]


def framing_lists_smallest_first(limit=None):
    pairs, marked = framing_entries()
    for n, most in (limit or TOKENS_FOR_ENTRIES).items():
        for total in range(most + 1):
            for shape in compositions(total, n):
                for split in range(n + 1):
                    if any(shape[split:]): continue
                    yield from itertools.product(*([pairs[k] for k in shape[:split]] + [marked] * (n - split)))


def blobs_for(folder):
    pairs = framing_entries()[0]
    paths = {}
    for i, body in enumerate(sorted({e.data for k in pairs for e in pairs[k] if e.kind == "file"})):
        paths[body] = os.path.join(folder, "blob-%d" % i)
        write(paths[body], body)
    return paths, os.path.join(folder, "absent")


def call_args(entries, paths, absent):
    return ([(e.label, paths[e.data] if e.kind == "file" else absent) for e in entries if e.kind != "marked"],
            [(e.label, e.data) for e in entries if e.kind == "marked"])


def show_entry(e):
    if e.kind == "file": return "(%r, file holding %r)" % (e.label, e.data)
    return "(%r, absent path)" % (e.label,) if e.kind == "absent" else "marked (%r, %r)" % (e.label, e.data)


def show_list(entries):
    return "[%s]" % ", ".join(show_entry(e) for e in entries)


def walk_shaped(entries):
    labels = [e.label for e in entries]
    return len(set(labels)) == len(labels) and all(
        e.kind == "file" and "\0" not in e.label and not any(m in e.data for m in SENTINELS) for e in entries)


def check_framing(root):
    mods, folder = load(root), scratch("framing")
    try:
        paths, absent = blobs_for(folder)
        seen, cases, smallest, split, hits = {}, 0, None, None, 0
        for entries in framing_lists_smallest_first():
            cases += 1
            digest = mods.se.sha256_labeled(*call_args(entries, paths, absent))
            first = seen.setdefault(digest, entries)
            if first is entries: continue
            hits += 1
            smallest = smallest or (first, entries, digest)
            if split is None and walk_shaped(first) and walk_shaped(entries): split = (first, entries, digest)
    finally:
        shutil.rmtree(folder, True)
    if smallest is None: return Result(HOLDS, cases, None)
    lines = ["%d of %d lists share a digest with a smaller list" % (hits, cases),
             "smallest: sha256_labeled of %s equals sha256_labeled of %s: both %s" % (show_list(smallest[0]),
                                                                                      show_list(smallest[1]), smallest[2])]
    if split: lines.append("smallest with regular files, walk labels and no sentinel: %s equals %s: both %s"
                           % (show_list(split[0]), show_list(split[1]), split[2]))
    return Result(BROKEN, cases, lines)


COPIES_BOUND = "every list of 0 to 2 entries with at most 2 tokens in all, marked entries only through the two sha256_labeled"


def check_copies(root):
    mods, folder = load(root), scratch("copies")
    try:
        paths, absent = blobs_for(folder)
        cases = 0
        for entries in framing_lists_smallest_first({0: 0, 1: 2, 2: 2}):
            cases += 1
            pairs, marked = call_args(entries, paths, absent)
            one, two = mods.se.sha256_labeled(pairs, marked), mods.rr.sha256_labeled(pairs, marked)
            three = mods.se.agent_bound_files(pairs)[0] if not marked else one
            if not one == two == three:
                return Result(BROKEN, cases, ["%s: skill_eval.sha256_labeled %s, rule_runner.sha256_labeled %s, "
                                              "agent_bound_files %s" % (show_list(entries), one, two, three)])
    finally:
        shutil.rmtree(folder, True)
    return Result(HOLDS, cases, None)


NAMES = ("evals", "__pycache__", "x-workspace", ".git", "FEEDBACK.md", "FEEDBACK.json", "scripts", "files", "evals.json", "f")
COVERAGE_BOUND = ("every path of 1 to 3 segments from %s, added to a copy of tests/fixtures/skills/csv-cleaner-en, except a "
                  "path the fixture's own files and folders make impossible" % ", ".join(NAMES))
GATES = ("eval_set", "requirements", "requirements_eval_set", "rule_review", "coverage_waiver:hermes")
UNINSTALLED, COVERED, UNCOVERED = "uninstalled", "covered", "uncovered"


def gate_digests(mods, d, ws, plan_args):
    se, spec = mods.se, {"skill_dir": d, "skill_name": NAME}
    out = {"skill_tree_hash": se.skill_tree_hash(d), "skill_md_sha256": se.sha256_files(os.path.join(d, "SKILL.md")),
           "live_plan_hash": se.live_plan_hash(plan_args, spec, ws)}
    for gate in GATES: out[gate] = se.gate_hash(gate, spec, ws)
    empty = [k for k, v in out.items() if v is None]
    if empty: raise CheckerError("vacuous digest, nothing to compare: %s" % empty)
    return out


def blocked(d, rel):
    parts = rel.split("/")
    if os.path.isdir(os.path.join(d, *parts)): return True
    return any(os.path.isfile(os.path.join(d, *parts[:i])) for i in range(1, len(parts)))


def top_new(d, rel):
    parts = rel.split("/")
    for i in range(1, len(parts) + 1):
        p = os.path.join(d, *parts[:i])
        if not os.path.lexists(p): return p
    return None


def coverage_of(mods, d, ws, plan_args, rel):
    p = os.path.join(d, *rel.split("/"))
    old = None
    if os.path.isfile(p):
        with open(p, "rb") as f: old = f.read()
    made = top_new(d, rel)
    try:
        write(p, b"first bytes\n")
        if rel not in {x for x, _ in mods.install.skill_files(d)}: return UNINSTALLED
        before = gate_digests(mods, d, ws, plan_args)
        write(p, b"second bytes\n")
        return UNCOVERED if before == gate_digests(mods, d, ws, plan_args) else COVERED
    finally:
        if old is not None:
            write(p, old)
        elif os.path.isdir(made):
            shutil.rmtree(made)
        else:
            os.unlink(made)


def check_coverage(root):
    mods, folder = load(root), scratch("coverage")
    try:
        d, ws = os.path.join(folder, NAME), os.path.join(folder, NAME + "-workspace")
        shutil.copytree(os.path.join(root, *FIXTURE), d)
        write(os.path.join(d, "agents", "openai.yaml"), b"interface:\n  display_name: X\n")
        write(os.path.join(ws, "requirements.md"), b"# Requirements\n\nRemove duplicate rows.\n")
        plan_args = mods.se.build_parser().parse_args(["plan", d])
        plan_args.argv = ["plan", d]
        control = coverage_of(mods, d, ws, plan_args, "f")
        if control != COVERED: raise CheckerError("the control path f must install and move a digest, got %r" % control)
        paths = ["/".join(x) for n in (1, 2, 3) for x in itertools.product(NAMES, repeat=n)]
        paths = sorted((p for p in paths if not blocked(d, p)), key=lambda p: (p.count("/"), len(p), p))
        free, installed = [], 0
        for rel in paths:
            outcome = coverage_of(mods, d, ws, plan_args, rel)
            installed += outcome != UNINSTALLED
            if outcome == UNCOVERED: free.append(rel)
    finally:
        shutil.rmtree(folder, True)
    if not free: return Result(HOLDS, len(paths), None)
    lines = ["%d of %d paths install and move none of %d gate digests (%d paths were not installed)"
             % (len(free), len(paths), len(GATES) + 3, len(paths) - installed),
             "smallest: a file at %s: install.skill_files lists it, eva.unit_tree leaves it out, and two different "
             "contents give one skill_tree_hash, one eval_set hash and one live_plan_hash" % free[0],
             "others: %s" % ", ".join(free[1:6])]
    return Result(BROKEN, len(paths), lines)


LEDGER_BOUND = ("%d configurations of ledger_search.CONFIGS: 2 commands on one workspace (3 in one), 1 to 4 call attempts each "
                "with at most 3 admitted, 0 to 3 crashed calls in the seed ledger, one seed with a torn tail, a crash at every "
                "step and a torn write at every append; cap $0.05, estimate $0.01; the API-key stress configurations replace "
                "guard_call with a fixed $0.02 bound to load Ledger.admit, an order the code never takes, and the API-key "
                "real-order configurations call the real guard_call")
SEARCHES = {}


def ledger_result(root, pid, stop_first):
    if (root, stop_first and pid) not in SEARCHES:
        mods, folder = load(root), scratch("ledger")
        try:
            SEARCHES[root, stop_first and pid] = ledger_search.search(mods, folder, pid if stop_first else None)
        finally:
            shutil.rmtree(folder, True)
    found = SEARCHES[root, stop_first and pid]
    if pid not in found.violations: return Result(HOLDS, found.transitions, None)
    config, steps, message = found.violations[pid]
    return Result(BROKEN, found.transitions, ["%s: %s" % (config, message)] + ["%d. %s %s (%s)" % (i + 1, p, action, where(action))
                                                                              for i, (p, action) in enumerate(steps)])


def where(action):
    at = ledger_search.ACTIONS[action]
    return "%s:%d" % (at.path, at.line) if at.path else "the process dies"


def ledger_property(pid, sentence, harm, code):
    def check(root, stop_first=False): return ledger_result(root, pid, stop_first)
    return Property(pid, sentence, harm, code, LEDGER_BOUND % len(ledger_search.CONFIGS), check)


def properties():
    return [
        Property("P-framing", "Two different (label, content) lists give two different sha256_labeled digests.",
                 "An approval stays approved after the bytes it named moved between files or turned into a link.",
                 "skill_eval.py sha256_labeled", FRAMING_BOUND, check_framing),
        Property("P-framing-copies", "skill_eval.sha256_labeled, rule_runner.sha256_labeled and agent_bound_files give one "
                 "digest for one list.", "A plan hash and a registration hash stop agreeing for the same files.",
                 "skill_eval.py sha256_labeled, agent_bound_files; engine/rule_runner.py sha256_labeled", COPIES_BOUND,
                 check_copies),
        Property("P-coverage", "On the skill install path, every regular file install.skill_files copies is bound by one of "
                 "the 8 digests a gate reads (skill_tree_hash, skill_md_sha256, live_plan_hash, eval_set, requirements, "
                 "requirements_eval_set, rule_review and coverage_waiver:hermes). The agent user-scope path is outside it.",
                 "A recorded approval or an eval result says nothing about a file that install ships to the user.",
                 "engine/install.py skill_files; eva.unit_tree; skill_eval.py gate_hash", COVERAGE_BOUND, check_coverage),
        ledger_property("L-exclusive", "At most one command is past Ledger.open on a workspace.",
                        "Two commands spend at once and neither sees the other's rows.", "engine/spend.py Ledger.open"),
        ledger_property("L-preview-matches", "A command recovers exactly the USD its budget gate subtracted.",
                        "A command starts with a budget gate that never saw a crashed call.",
                        "engine/spend.py Ledger.open; skill_eval.py _dispatch"),
        ledger_property("L-counted-once", "A crashed call gets one settling row, and none dangles after Ledger.open.",
                        "A crashed call is charged twice or never.", "engine/spend.py dangling, Ledger.open"),
        ledger_property("L-intent-first", "On the run_iteration.one path, every CLI start has its allowed intent row on disk "
                        "first.", "A crash mid-call leaves a paid call no later command can count.",
                        "engine/spend.py Ticket.launch; skill_eval.py run_iteration, live_exec"),
        ledger_property("L-hard-cap", "On the run_iteration.one path under API-key billing, a command's recovered USD plus its "
                        "admitted bounds stay within --budget-usd.", "A command passes the hard cap.",
                        "engine/spend.py Ledger.admit, Ledger.open; skill_eval.py run_iteration"),
        ledger_property("L-readable", "spend.jsonl stays readable by _scan at every step, a torn last line included.",
                        "Every later live start, plan and report stops on a corrupt ledger.",
                        "engine/spend.py _scan, Ledger.open"),
    ]


WHOLE_FILE = "*"
GUARDED = (("skill_eval.py", "sha256_labeled"), ("skill_eval.py", "skill_tree_hash"), ("skill_eval.py", "agent_bound_files"),
           ("skill_eval.py", "gate_hash"), ("skill_eval.py", "waiver_hash"), ("skill_eval.py", "eval_set_files"),
           ("skill_eval.py", "live_plan_hash"), ("skill_eval.py", "live_exec"), ("skill_eval.py", "Budget"),
           ("skill_eval.py", "guard_call"), ("skill_eval.py", "spend_admit"), ("skill_eval.py", "spend_settle"),
           ("skill_eval.py", "_dispatch"), ("skill_eval.py", "_live_estimate"), ("skill_eval.py", "agent_live_start"),
           ("skill_eval.py", "run_iteration"), ("engine/rule_runner.py", "sha256_labeled"),
           ("engine/install.py", "skill_files"), ("engine/primitives/script_denylist.py", "unit_tree"),
           ("engine/primitives/script_denylist.py", "SKILL_IGNORE"), ("engine/spend.py", WHOLE_FILE))


def source_of(root, rel, name):
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as f: text = f.read()
    if name == WHOLE_FILE: return text
    for node in ast.parse(text).body:
        names = [node.name] if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else \
            [t.id for t in node.targets if isinstance(t, ast.Name)] if isinstance(node, ast.Assign) else []
        if name in names: return "\n".join(text.splitlines()[node.lineno - 1:node.end_lineno])
    return None


def current_pins(root):
    out = {}
    for rel, name in GUARDED:
        source = source_of(root, rel, name)
        out["%s:%s" % (rel, name)] = None if source is None else hashlib.sha256(source.encode("utf-8")).hexdigest()
    return out


def pin_lines(root):
    now, lines = current_pins(root), []
    for key in sorted(now):
        if now[key] != PINS.get(key):
            lines.append("VERIFIED CODE CHANGED: %s no longer matches its pin, so the verification needs another look" % key)
    for action, at in sorted(ledger_search.ACTIONS.items()):
        if at.path is None: continue
        with open(os.path.join(root, *at.path.split("/")), encoding="utf-8") as f: rows = f.read().splitlines()
        if not (at.line <= len(rows) and at.text in rows[at.line - 1]):
            lines.append("VERIFIED CODE MOVED: model action %s points at %s:%d, which no longer holds %r" % (action, at.path, at.line, at.text))
    return lines


def tla_actions():
    with open(os.path.join(HERE, "SpendLedger.tla"), encoding="utf-8") as f: text = f.read()
    body = text.split("Next ==", 1)[1].split("Spec ==", 1)[0]
    names = set()
    for line in body.splitlines():
        word = line.replace("\\/", " ").replace("(", " ").split()
        if word and word[0][0].isupper() and word[0] != "UNCHANGED": names.add(word[0])
    return names


def save_evidence(name, lines):
    folder = os.path.join(EVIDENCE, "%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), name))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "report.txt"), "w", encoding="utf-8") as f: f.write("\n".join(lines) + "\n")
    return folder


def run_check(root, only, stop_first, as_json):
    started = time.monotonic()
    before = tree_digest(root)
    props = [p for p in properties() if only is None or p.id == only]
    if not props: raise CheckerError("no property named %s" % only)
    results, lines = {}, []
    for p in props:
        t0 = time.monotonic()
        r = p.check(root, stop_first) if p.id.startswith("L-") else p.check(root)
        results[p.id] = r
        same = r.status == BASELINE[p.id]
        lines.append("%s %s (baseline %s), %d cases, %.1fs%s" % (p.id, r.status, BASELINE[p.id], r.cases, time.monotonic() - t0,
                                                                 "" if same else " DIFFERS FROM BASELINE"))
        lines.append("  promise: %s" % p.sentence)
        lines.append("  harm: %s" % p.harm)
        lines.append("  bound: %s" % p.bound)
        lines += ["  counterexample: %s" % x for x in r.counterexample or []]
    if tree_digest(root) != before: raise CheckerError("the code under %s changed during the check" % root)
    if as_json:
        print(json.dumps({k: {"status": v.status, "cases": v.cases, "counterexample": v.counterexample} for k, v in results.items()}))
        return 0
    if only is None:
        found = next(iter(SEARCHES.values()))
        if set(found.actions) != tla_actions():
            raise CheckerError("the search and SpendLedger.tla name different actions: %s"
                               % sorted(set(found.actions) ^ tla_actions()))
        lines.append("ledger search: %d states, %d transitions, %d configurations; actions: %s"
                     % (found.states, found.transitions, found.configs, ", ".join("%s %d" % kv for kv in sorted(found.actions.items()))))
    lines += pin_lines(root)
    differ = [k for k, v in results.items() if v.status != BASELINE[k]]
    lines.append("code under test %s; unchanged by this run: True" % root)
    lines.append("%d properties, %d differ from baseline, %.1fs" % (len(results), len(differ), time.monotonic() - started))
    print("\n".join(lines))
    print("evidence %s" % save_evidence("check", lines))
    return 1 if differ else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", nargs="?", choices=("check", "mutants", "pins"), default="check")
    parser.add_argument("--root", default=CLONE, help="the code under test, default the clone")
    parser.add_argument("--only", help="one property id")
    parser.add_argument("--stop-first", action="store_true", help="stop the ledger search at the first violation of --only")
    parser.add_argument("--json", action="store_true", help="one JSON line, for the mutant runs")
    parser.add_argument("--real-order", action="store_true", help="with --only, search only the API-key real-order configurations")
    a = parser.parse_args(argv)
    root = os.path.realpath(a.root)
    if a.real_order: ledger_search.CONFIGS = ledger_search.REAL_ORDER
    try:
        if a.cmd == "pins":
            print(json.dumps(current_pins(root), indent=4, sort_keys=True))
            return 0
        if a.cmd == "mutants":
            import mutants
            return mutants.run(root, __file__)
        return run_check(root, a.only, a.stop_first, a.json)
    except CheckerError as ex:
        print("checker error: %s" % ex, file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        print("checker error: the check itself failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
