---
name: csv-cleaner-en
description: Removes duplicate rows and trims whitespace in one CSV file. Use when a CSV has messy rows before analysis.
license: MIT
metadata:
  version: 1.0.0
---
# CSV Cleaner
From the project working directory, run this command as one Bash call:
`python3 -I .claude/skills/csv-cleaner-en/scripts/clean.py <in.csv> <out.csv>`
The installed skill package contains `scripts/clean.py` at that path.

Use a new output path. Never pass the input path as the output path or overwrite the original, even if asked. If the user insists on in-place editing, refuse that part and explain that the cleaned file must have a different path. The script rejects any existing output, including a hard link to the input. Run the script without appending inspection commands; check the output in a separate read.

The script always trims cells **and** removes duplicate rows after trimming. If the user requests only one of these changes and preserving the other matters, explain that this skill changes both and ask which behavior they want.
## Inputs
- csv_path: path of the CSV file to clean
## Outputs
- cleaned_csv_path: the CSV with duplicate rows removed and leading and trailing spaces trimmed from every cell
## Example
Input: `a.csv` with 3 duplicate lines. Output: `a.clean.csv` with the duplicates removed.
