I couldn't clean `survey_responses.csv`, and I haven't changed anything.

- **Cleaner:** `clean.py` rejected the input with "input must be a regular workdir file without aliases".
- **File check:** Reading `survey_responses.csv` in the working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-mbjxqseu/work`) says the file doesn't exist. If it were a symlink or alias, the cleaner would have refused it for the same reason.
- **Tool limits:** Bash here only allows the `clean.py` call, so I couldn't list the directory or look for the file elsewhere.

Can you tell me the file's actual path, or put a regular copy (not a symlink or hard link) in the working directory? Once it's there I'll run the cleaner and write the result to `survey_responses.clean.csv`, leaving the original as it is.

The cleaner always trims whitespace and removes duplicate rows after trimming, so both of your requested fixes will be applied.