# data-tidy requirements

## Goal
- Clean every CSV file in the data/raw folder into data/clean with the registered csv-cleaner skill, and report the row counts.

## Users
- Analysts who receive raw exports and want clean copies before analysis.

## Capabilities
- read, to list and inspect the raw files.
- write, to create the data/clean folder.
- shell, because the csv-cleaner skill runs its bundled script.
- skill, to load the registered skill csv-cleaner@1.0.0.

## Success criteria
- data/clean/orders.csv exists after a run on the sample folder (file_exists).
- The agent never overwrites a file in data/raw (grader question).
- The final answer is a Markdown table of file names and row counts (markdown_table).

## Non-goals
- Changing values inside cells beyond what csv-cleaner does.
- Merging files.

## Target tools
- claude, codex, opencode, hermes.

## Open questions
- None.
