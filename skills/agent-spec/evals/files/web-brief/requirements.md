# web-brief requirements

## Goal
- Answer one research question with a short brief that cites web sources the agent fetched.

## Users
- Product managers who need a quick, sourced overview of a topic.

## Capabilities
- web_search, to find candidate sources.
- web_fetch, to read each source before citing it.
- write, to save the brief as brief.md.

## Success criteria
- The brief cites at least 3 sources that a tool call fetched (min_verified_sources, min 3).
- The final answer stays under 1500 characters (max_chars).
- Every claim has a source (grader question).

## Non-goals
- Opinions or recommendations without a source.

## Target tools
- Claude Code only. Nobody on the team uses the other tools.

## Open questions
- Whether brief.md should also hold the full source list.
