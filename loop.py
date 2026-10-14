#!/usr/bin/env python3
"""Builder <-> Eva loop. Usage: python3 loop.py <unit_path> --builder "<cmd>" [--max-rounds 3]
The builder command gets env EVA_FEEDBACK=<path to FEEDBACK.md> and EVA_ROUND, must edit the unit, then exit 0.
Exit codes: 0 PASS, 1 still FAIL (or no --builder), 2 builder exited nonzero (loop stopped)."""
import argparse, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva
BUILDER_FAILED = 2
a = argparse.ArgumentParser(); a.add_argument("path"); a.add_argument("--builder"); a.add_argument("--max-rounds", type=int, default=3)
a = a.parse_args()
for rnd in range(1, a.max_rounds + 1):
    r = eva.check(a.path)
    open(os.path.join(a.path, "FEEDBACK.md"), "w", encoding="utf-8").write(eva.feedback_md(r, rnd))
    print("round %d: %s %.2f" % (rnd, r.verdict, r.score))
    if r.verdict == "PASS": sys.exit(0)
    if not a.builder: print("no --builder; stop. see FEEDBACK.md"); sys.exit(1)
    env = dict(os.environ, EVA_FEEDBACK=os.path.join(a.path, "FEEDBACK.md"), EVA_ROUND=str(rnd))
    rc = subprocess.run(a.builder, shell=True, env=env).returncode
    if rc != 0:
        print("builder failed: exit %d in round %d; stop. see FEEDBACK.md" % (rc, rnd), file=sys.stderr)
        sys.exit(BUILDER_FAILED)
r = eva.check(a.path); print("final: %s %.2f" % (r.verdict, r.score))
sys.exit(1 if r.verdict == "FAIL" else 0)
