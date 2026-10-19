I couldn't clean the file. `survey_responses.csv` isn't in the working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-dywz89wb/work`).

- **Cleaning script:** I ran the csv-cleaner script as `clean.py survey_responses.csv survey_responses.clean.csv`. It stopped with "input must be a regular workdir file without aliases". That fits the file being missing, or being a symlink or alias.
- **Existence check:** Reading the path directly returned "File does not exist".
- **Directory listing:** My attempt to list the directory was blocked. This session only lets me run the cleaner script, so I couldn't look for the file elsewhere.

Please send me one of these:
- The full path to the file. If it's outside the working directory, I'd need it copied or moved in.
- A confirmation of whether it's a symlink or alias. If so, copy it in as a regular file.

Once it's in place, I'll write the output to `survey_responses.clean.csv` and leave your original untouched.

The skill always trims whitespace and removes duplicate rows after trimming. That matches what you asked for. One thing to be aware of: rows count as duplicates only if every column matches after trimming. If any column differs between repeated submissions, such as a timestamp or ID, those rows will survive. I can check for that once I can read the file.