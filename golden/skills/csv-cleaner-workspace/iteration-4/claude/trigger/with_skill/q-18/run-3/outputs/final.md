I couldn't merge the files because `jan.csv`, `feb.csv` and `mar.csv` don't exist in the working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-0r1g7d97/work`). The Read tool reported "File does not exist" for all three.

I also couldn't search for them. Bash in this session only allows a `clean.py` invocation with two paths, so `ls` and `find` were blocked.

The `csv-cleaner` skill doesn't fit this task either. It removes duplicates and trims whitespace in a single CSV, and it doesn't merge files.

Can you tell me where the three files are, or copy them into the working directory? Once I can read them, I'll write the merged file with the Write tool. I'd keep the header from `jan.csv` and append the data rows from the other two, after checking that the three headers match. If you'd rather I merge them a different way, such as adding a month column, tell me.