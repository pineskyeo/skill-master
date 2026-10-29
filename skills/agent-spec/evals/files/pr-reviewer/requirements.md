# pr-reviewer requirements

## Goal
- Review the changes of one pull request and report each finding with file and line evidence, without changing any file.

## Users
- Developers who want a second reader before they merge a branch.

## Capabilities
- read, to open each changed file with line numbers.
- search, to find the changed files and the callers of changed functions.

## Success criteria
- At least 2 findings cite path:line evidence from files the agent read (file_line_citations, min 2).
- The run changes no file (no_writes).
- Every finding says why the cited line is a problem (grader question).

## Non-goals
- Fixing the code. The developer applies fixes.
- Approving or merging the pull request.

## Target tools
- claude, codex, opencode, hermes.

## Open questions
- None.
