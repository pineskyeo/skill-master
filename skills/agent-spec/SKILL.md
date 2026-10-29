---
name: agent-spec
description: Drafts the AGENT.md v2 file of one agent from its requirements.md, with typed success criteria and the Tools and Memory sections that the static check reads. Use when an agent unit has a requirements file and its AGENT.md is not written yet or must be redrafted.
metadata:
  version: "1.0.0"
---
# Agent spec

Draft the `AGENT.md` of one agent from its `requirements.md`. AGENT.md is the single source of the agent. The engine renders the four tool wrappers from it, and the static check grades it with `rules/agent-structure.rules.json` and `schemas/agent-frontmatter.schema.json`. You only draft this file. You run no engine command, and you write no scenario, wrapper, or eval file.

## Inputs

- The agent's `requirements.md`, with the headings Goal, Users, Capabilities, Success criteria, Non-goals, Target tools, and Open questions.
- The output path. If the request names none, write `agents/<name>/AGENT.md`, where `<name>` is the agent name.

## Outputs

- One `AGENT.md` at the output path, in English ASCII text.
- A reply of a few lines that names the file, each requirement that the frontmatter could not express, and each open question carried over from the requirements.

## Frontmatter

Write these keys in this order, each on its own line:

- `name`. The agent name, in lowercase letters, digits, and single hyphens. It equals the name of the folder that holds AGENT.md.
- `description`. One sentence that says what the agent does, then a sentence that starts with "Use when" and names the request that should start it. Leave out colons, the character `#`, and the characters `<` and `>`.
- `goal`. One sentence, from the Goal heading. Leave out colons and the character `#`. The frontmatter reader refuses a plain value that holds a colon followed by a space, and drops everything after a space and `#`.
- `capabilities`. A flow list such as `[read, search]`, with words from read, search, write, shell, web_fetch, web_search, delegate, and skill only. Take them from the Capabilities heading. Leave out every capability that no step of the agent uses.
- `model_policy`. Write `judgment`. It is the only role that the tool targets map today.
- `success_criteria`. A block list. Each item is one typed JSON object in single quotes, as in the example.
- `targets`. Always `[claude, codex, opencode, hermes]`. The schema requires exactly these four. When the requirements name fewer tools, keep all four and report the difference.
- `skills`. Only when the requirements list registered skills. Write a flow list of single-quoted `<name>@<version>` pins, such as `['csv-cleaner@1.0.0']`, and add `skill` to the capabilities.
- `version`. Write `1.0.0` for a new agent.

Write no other key. Never write a `tools` key. It marks the file as the old v1 format.

## Success criteria

Turn each success criterion of the requirements into one criterion object. Use a machine type when one fits, and keep at least one machine type in the list.

The engine grades every AGENT.md criterion on every scenario of the agent. Put a criterion here only when it holds for every request the agent takes. A criterion about one sample input, such as a file that one test folder produces, belongs in a scenario. Leave it out of AGENT.md and name it in the reply.

| Type | Keys | Use it when the agent must |
|---|---|---|
| `file_line_citations` | `min` | cite `path:line` evidence from files it read |
| `no_writes` | none | change no file |
| `max_chars` | `max` | keep the final answer under a length |
| `markdown_table` | none | answer with a Markdown table |
| `min_verified_sources` | `min` | cite web sources that a tool call fetched |
| `command_ran` | `regex` | run a shell command, such as `git log` |
| `command_absent` | `regex` | never run a shell command, such as `git push` |
| `file_exists` | `path` | leave a file in its work folder |
| `llm` | `text` | meet a condition that only a reader can judge |

Follow these rules for the objects:

- Write each object on one line, wrapped in single quotes: `- '{"type": "no_writes"}'`.
- Use ASCII text only. Inside the single quotes, write an apostrophe as two apostrophes.
- In a `regex`, write every backslash as two backslashes, because the text is JSON. Write `\\.` for a literal dot and `\\s` for a space. A single backslash makes the object invalid, or silently changes `\b` into another character. Match the command, as in `git push`, not a whole command line.
- Use `command_ran` and `command_absent` only with the `shell` capability, and `file_exists` only with `write`.
- Write an `llm` text as a yes or no question about the final answer or the work, such as "Does every finding name the file it is about?"

## Body

After the frontmatter, write these parts in English:

1. A title line, `# <Agent title>`.
2. A short role paragraph from Goal and Users.
3. `## Steps`, a numbered list of what the agent does from request to final answer.
4. `## Tools`, one bullet per capability that says when the agent uses it. The static check needs a heading with the word "Tools".
5. `## Rules`, the non-goals as plain orders, such as "Never run git push."
6. `## Memory & Handoff`, what the agent keeps between turns and what it hands over at the end. The static check needs a heading with the word "Memory", "Handoff", or "Context".

## Rules

- Draft from the requirements only. When a heading is missing or unclear, write the closest valid value and name the gap in the reply.
- Keep the Open questions of the requirements in the reply. Do not answer them yourself.
- If an AGENT.md already exists at the output path, read it first and keep what still matches the requirements.

## Example

The requirements say: the agent reviews a pull request read-only, cites at least two file and line references, and changes no file.

```markdown
---
name: pr-reviewer
description: Reviews the changes of one pull request read-only and reports findings with file and line evidence. Use when a person asks for a review of a branch or a pull request.
goal: Review one pull request without changing files and report each finding with file and line evidence.
capabilities: [read, search]
model_policy: judgment
success_criteria:
  - '{"type": "file_line_citations", "min": 2}'
  - '{"type": "no_writes"}'
  - '{"type": "llm", "text": "Does every finding say why the line is a problem?"}'
targets: [claude, codex, opencode, hermes]
version: 1.0.0
---
# PR reviewer

Reviews the diff of one pull request for a developer before merge.

## Steps
1. Find the changed files with search.
2. Read each changed file with line numbers.
3. Report each finding as path:line and the reason.

## Tools
- search finds the changed files and the callers of changed functions.
- read shows each file with line numbers, which the citations need.

## Rules
- Never edit, create, or delete a file.

## Memory & Handoff
Keeps the list of reviewed files during the session. Hands over the findings as the final answer.
```

The reply: "Wrote agents/pr-reviewer/AGENT.md. All requirements fit the frontmatter. No open questions."
