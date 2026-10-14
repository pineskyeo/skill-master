import csv, sys
seen, rows = set(), []
for r in csv.reader(open(sys.argv[1], newline="")):
    r = [c.strip() for c in r]
    if tuple(r) not in seen:
        seen.add(tuple(r)); rows.append(r)
csv.writer(open(sys.argv[2], "w", newline="")).writerows(rows)
