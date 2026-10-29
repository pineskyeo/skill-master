# dep-auditor requirements

## Goal
- List the outdated Python dependencies of one project and rank them by how far behind they are, without installing anything.

## Users
- Developers who plan a dependency update.

## Capabilities
- read, to read requirements.txt and pyproject.toml.
- search, to find where a dependency is imported.
- shell, to run pip list --outdated.

## Success criteria
- The agent runs pip list --outdated (command_ran).
- The agent never runs pip install or pip uninstall (command_absent).
- The run changes no file (no_writes).
- The final answer is a Markdown table with the package, the installed version, and the latest version (markdown_table).

## Non-goals
- Upgrading packages or editing requirement files.

## Target tools
- claude, codex, opencode, hermes.

## Open questions
- None.
