I couldn't clean the file, because `survey_responses.csv` isn't in the working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-dsctgbj4/work`).

- **Cleaner:** I ran `clean.py` on `survey_responses.csv`. It refused with "input must be a regular workdir file without aliases."
- **Read check:** Reading the path directly returned "File does not exist."
- **Directory listing:** I tried to list the directory, but the shell here only allows the `clean.py` call, so I couldn't look for a misnamed copy.

Nothing was created or changed.

Where is the file? If it's elsewhere, copy it into the working directory and give me the exact filename. It also fails if the path is a symlink or alias, so it needs to be a regular file. Once it's there, I'll write the result to a new file, `survey_responses.clean.csv`. I won't overwrite the original.

The skill always does both things together. It trims the trailing spaces in every column and removes duplicate rows after trimming. That matches what you asked for.