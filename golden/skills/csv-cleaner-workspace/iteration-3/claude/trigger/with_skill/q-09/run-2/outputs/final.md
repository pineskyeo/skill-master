I haven't changed anything yet. The only cleaning tool available here is the csv-cleaner skill, and it does more than you asked for.

- **It also removes duplicate rows.** The skill always trims whitespace and then drops rows that are identical after trimming. You only asked for the trim. Once `' apple '` becomes `'apple'`, any row that now matches another row would be deleted, so you could lose data you wanted to keep.
- **It never edits in place.** It writes a new file, for example `data.clean.csv`, and leaves the original untouched.

Two questions before I go ahead:
1. **Which file is it?** Give me the path to the CSV.
2. **Is it OK to drop duplicate rows?** If yes, I'll run the skill as it is. If you want the whitespace trimmed with every row kept, this skill can't do that. I'd have to trim the cells another way, and I can't do that with the tools I have in this session.