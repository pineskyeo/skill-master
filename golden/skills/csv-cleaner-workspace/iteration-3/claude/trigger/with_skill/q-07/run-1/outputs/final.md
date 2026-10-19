I haven't run anything yet, because the only tool available here does more than you asked for.

The `csv-cleaner` skill is the only way I can process the file in this environment. It does two things you can't turn off:

- **Trims whitespace:** it strips leading and trailing spaces from every cell.
- **Dedupes after trimming:** it removes rows that match once trimmed, so it would also drop near-duplicates, such as two rows that differ only by a stray space.

You asked to remove only *exact* duplicate lines. Running the skill could change cell contents and remove rows that aren't exact duplicates. That would make `transactions_dedup.csv` differ from what you asked for.

How would you like to proceed?

1. **Run the skill anyway.** You get whitespace-trimmed cells and rows deduplicated after trimming. This is fine if your data has no meaningful whitespace differences.
2. **Use a different method.** An exact, order-preserving line dedupe (`awk '!seen[$0]++'`) does what you described. The shell is currently restricted to the `clean.py` call, so you'd need to lift that restriction or run the command yourself.

The skill would write to a new file, so `transactions_2025.csv` would stay untouched either way.