import csv, os, sys
if len(sys.argv) != 3:
    raise SystemExit("usage: clean.py <in.csv> <out.csv>")
if os.path.realpath(sys.argv[1]) == os.path.realpath(sys.argv[2]):
    raise SystemExit("input and output must be different files")
seen, rows = set(), []
for r in csv.reader(open(sys.argv[1], newline="")):
    r = [c.strip() for c in r]
    if tuple(r) not in seen:
        seen.add(tuple(r)); rows.append(r)
csv.writer(open(sys.argv[2], "w", newline="")).writerows(rows)
