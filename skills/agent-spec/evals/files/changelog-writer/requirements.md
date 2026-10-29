# changelog-writer requirements

## Goal
- Write CHANGELOG.md entries for the commits since the last tag, grouped into Added, Changed, and Fixed.

## Users
- Maintainers who prepare a release in their local clone.

## Capabilities
- read, to read the existing CHANGELOG.md.
- search, to find the changed files of a commit.
- shell, to run git log and git describe.
- write, to update CHANGELOG.md.

## Success criteria
- The agent runs git log to collect the commits (command_ran).
- The agent never runs git push or git tag (command_absent).
- CHANGELOG.md exists in the work folder after the run (file_exists).
- Each entry names its commit hash (grader question).

## Non-goals
- Pushing, tagging, or publishing a release. The maintainer does that.
- Rewriting entries of earlier releases.

## Target tools
- claude, codex, opencode, hermes.

## Open questions
- None.
