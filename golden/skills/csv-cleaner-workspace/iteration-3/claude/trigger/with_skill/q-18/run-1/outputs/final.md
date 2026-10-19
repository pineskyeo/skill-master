I couldn't merge the files because `jan.csv`, `feb.csv` and `mar.csv` don't exist in the working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-h3vllg9z/work`). Reading each one returned "File does not exist."

My Bash tool is also restricted in this session. It rejected a plain `ls` and `head`, saying only a `clean.py` invocation with two paths is allowed. So I couldn't search other directories for the files, and I couldn't run a merge command even if they were there.

The `csv-cleaner` skill only handles one CSV at a time (dedupe and trim whitespace), so it wouldn't do this merge.

To go ahead, I need one of these from you:
- The actual path to the three files, if they're somewhere else.
- The files placed in the working directory.
- Bash restrictions lifted, so I can run a merge command. I could also write the merged file with the Write tool once I can read the inputs.

Once I can see the files, I'll check that the headers match before concatenating, so the merged file has a single header row.