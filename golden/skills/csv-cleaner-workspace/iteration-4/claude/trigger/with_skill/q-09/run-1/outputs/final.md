I haven't changed anything yet. I need two answers from you first.

1. **Which file?** I don't have the CSV's path, and I can't list the directory in this session. What's the file name?

2. **Is it OK to drop duplicate rows?** The csv-cleaner skill always trims whitespace and also removes duplicate rows after trimming. You only asked for the trim. If your file has rows that differ only by stray whitespace, such as `' apple '` and `'apple'`, trimming makes them identical and the script would delete one. If each row is a separate record, like orders or log entries, that would lose data.
   - If dropping duplicates is fine, I'll run the skill as is.
   - If you need every row kept, this skill isn't the right tool, and I'd trim the cells another way.

The cleaned file has to be written to a new path, for example `yourfile.clean.csv`. The skill won't overwrite the original.