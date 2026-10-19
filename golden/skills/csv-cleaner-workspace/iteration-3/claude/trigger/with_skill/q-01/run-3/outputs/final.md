I still haven't deduped anything. I can't find `customers_export.csv`, and I can't search for it from here.

- **Script result:** `clean.py` rejected the file with "input must be a regular workdir file without aliases". That means the path is missing, a symlink, or outside the working directory.
- **Read result:** Read says the file doesn't exist at `/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-5dc__nzz/work/customers_export.csv`.
- **Search limits:** Bash in this session only allows the `clean.py` call, and Read can't list folders. The search agent therefore couldn't look either.

To go on, I need one of these:
1. The file's exact path or filename, if it's somewhere other than the working directory.
2. The file copied into the working directory as a regular file, not a symlink or alias.
3. The output of this command, run yourself, if you're unsure where it is: `find /private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-5dc__nzz/ -iname '*customer*export*.csv' -exec ls -ld {} +`

Once the file is in place, I'll write the result to `customers_export.clean.csv` and leave the original alone. The cleaner also trims leading and trailing spaces in every cell, so rows that differ only by stray spaces will count as duplicates.