I couldn't merge the files because `jan.csv`, `feb.csv` and `mar.csv` don't exist in the working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-wwoejqyi/work`). Reading each one returned "File does not exist."

Two other things came up:
- **Bash is locked down.** It only allows an installed `clean.py` call with two paths, so I couldn't list the directory or search for the files elsewhere. That script also doesn't merge files.
- **The `csv-cleaner` skill doesn't fit.** It dedupes and trims a single CSV, so I didn't use it.

Where are the three files? If you give me their full paths, I'll read them and write the merged file with the Read and Write tools. I'd keep one header row and append the rows from Jan, Feb and Mar in that order. I'd save the result as `merged.csv` unless you want a different name.