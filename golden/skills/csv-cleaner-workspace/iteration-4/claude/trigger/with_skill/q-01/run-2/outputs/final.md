I couldn't dedupe `customers_export.csv` because the file isn't in the working directory.

- **Cleaner script:** I ran the csv-cleaner script and it rejected the input. It says the input "must be a regular workdir file without aliases".
- **File check:** Reading `customers_export.csv` in `/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-tcd_t4vg/work` returned "File does not exist". The Bash tool is restricted to the cleaner script here, so I couldn't list the directory to look for it elsewhere.

I haven't changed or created any files.

**What I need from you:** the correct path to the file, or a copy of it placed in the working directory above. If it's a symlink or alias, a real copy will work.

**Trimming:** The cleaner removes duplicate rows and also trims leading and trailing whitespace in every cell. It can't do only one of the two. Trimming can make rows that differ only by stray spaces count as duplicates, and it changes the remaining cell values. If you want duplicates removed without any trimming, tell me. The cleaner can't do that, so I'd need a different approach.

The cleaned copy will be written to a new file, `customers_export.clean.csv`. The original won't be touched.