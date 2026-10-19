I haven't run anything yet, because the only tool I can use here would change more than you asked for.

The shell is limited to the csv-cleaner script, `clean.py`. It does two things you didn't ask for:
- **Trims cells:** it strips leading and trailing whitespace from every cell, so the output wouldn't match your original rows apart from the duplicates.
- **Dedupes after trimming:** it would also remove rows that differ only by whitespace, such as `"Acme, 50"` and `"Acme,50 "`. Those aren't exact duplicates.

You asked for exact duplicate lines only, and I can't run plain exact-line dedup (for example `awk '!seen[$0]++'`) in this environment.

Which would you like?
1. **Run the cleaner as it is.** The output goes to `transactions_dedup.csv` with trimmed cells and whitespace-insensitive dedup. This is fine if your data has no meaningful whitespace differences.
2. **Run exact-line dedup.** I'd need the shell restriction lifted or a different way to run commands.

If you pick 1, I'll run `python3 -I .claude/skills/csv-cleaner/scripts/clean.py transactions_2025.csv transactions_dedup.csv`. The original file stays untouched.