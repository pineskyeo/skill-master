# Meeting notes, docs sync

Attendees: Priya, Tom, Ana

- Tom: contributors forget to add a CHANGELOG.md entry, or they write one in the wrong format.
- We want a skill that drafts one changelog entry from the staged git diff.
- Format we use: a line under the "Unreleased" heading, starting with Added, Changed, Fixed, or Removed, then a short sentence.
- Priya: it should only draft the entry text and show it. Writing into CHANGELOG.md stays with the contributor.
- Ana: it should not try to summarize a whole release, only the one change.
- Not decided: what to do when the diff touches only tests. Skip or write "Changed"?
- Should work in Claude Code and Codex like our other skills.
