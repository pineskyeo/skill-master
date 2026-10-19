I couldn't merge the files because `jan.csv`, `feb.csv` and `mar.csv` aren't in the working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-93k3la70/work`). Reading each one returned "File does not exist".

I also couldn't list the directory to look for them under another name or location. The Bash tool here only allows a single `clean.py` invocation with two paths, so `ls`, `head` and `cat` were all refused.

The `csv-cleaner` skill won't do this job. It dedupes and trims whitespace in one CSV, and it doesn't combine files.

To go ahead, I need one of these from you:
- **The correct paths**, if the files are somewhere else.
- **The files placed in the working directory**.
- **Bash access** that allows listing and writing files, so I can find them and write the merged file.

When I do have the files, I'll check that the three headers match before stacking the rows under a single header. I'll write the result to something like `merged.csv` and leave the originals untouched. Tell me if you want a different output name or want duplicate rows removed.