I haven't run anything yet. The only tool I can use here is the `csv-cleaner` script, and it does more than you asked for.

- **What you asked for:** remove exact duplicate lines and keep everything else as it is.
- **What the script does:** it trims leading and trailing whitespace in every cell, then removes duplicates. Rows that differ only by whitespace would be merged, even though they aren't exact duplicates. The remaining rows would also have their whitespace changed.

I can't switch the trimming off or do an exact-match dedup another way, because Bash only allows that script call. Which would you like?

1. **Run the script anyway.** I'd write `transactions_dedup.csv` with trimmed cells and duplicates removed, and it would never overwrite `transactions_2025.csv`.
2. **Keep exact-match only.** I'd need Bash allowed to run something like `awk '!seen[$0]++'`, which keeps the first occurrence of each line and the original order. Alternatively I can do it another way if you tell me what's permitted.