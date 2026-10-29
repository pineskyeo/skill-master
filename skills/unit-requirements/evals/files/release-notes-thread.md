# Thread from the #platform channel

**Dana (release manager):** Every Friday I spend an hour turning merged pull requests into release notes. Can we get an agent that does the first draft?

**Omar:** It would need to read the merged PR titles and labels from the repo. We tag them `feature`, `fix`, and `internal`. Internal ones should not show up in the notes.

**Dana:** Right. Output is one markdown file, `RELEASE_NOTES.md`, with a Features section and a Fixes section. Each line should end with the PR number so I can check it.

**Omar:** It should run `git log` itself to find the merges since the last tag. It must never push, tag, or publish anything. Dana publishes by hand.

**Dana:** Keep it under 3000 characters, the release page cuts off after that.

**Lee:** Which tools does this need to work in? We use Claude Code mostly, some of us are on Codex.

**Dana:** No idea about OpenCode or Hermes, nobody here uses them.
