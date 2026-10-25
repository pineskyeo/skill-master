# Feedback loop protocol

1. **Submit.** A builder agent creates a skill or agent folder in `submissions/<name>/`.
2. **Check.** Run `python3 eva.py check <path>` (or add `--json`).
3. **Feedback.** On FAIL, `python3 eva.py feedback <path> --round N` writes `FEEDBACK.md` and `FEEDBACK.json`. Fix the required items first.
4. **Resubmit.** The builder applies only the fix instructions in FEEDBACK.md and submits again. At most 3 rounds.
5. **Finish.** On PASS, move the folder to `golden/` or the production folder and refresh the index with `eva.py index`. If it still fails after 3 rounds, hand it to a person.

Automation: `python3 loop.py <path> --builder "<builder command>" --max-rounds 3`
(The builder receives the environment variables `EVA_FEEDBACK` and `EVA_ROUND`. If the builder exits with a nonzero code, the loop stops and reports the failure with exit code 2.)

Grading a run transcript: `python3 eva.py check <agent_path> --transcript run.json`

run.json format (a JSON object):
```json
{
  "scenario": "<scenario id>",
  "workspace": "/abs/path/to/repo",
  "tool_calls": [
    "web_search",
    {"tool": "web_fetch", "input": {"url": "https://example.org/a"}, "result": "<content the tool returned>"}
  ],
  "tool_results": ["<tool result text>", "..."],
  "final": "<final answer>"
}
```
- Required: `scenario` (string), `tool_calls` (list), `final` (string). Optional: `workspace`, `tool_results`.
- Each `tool_calls` entry is a tool name string or a `{"tool": str, "input": str|object(optional), "result": str(optional)}` object. Any other field or type is a format error.
- Attach tool results to each call's `result`, or collect them in the top-level `tool_results` (a list of strings). You may use both.
- Source criteria count **only URLs that actually appear in tool results**. A URL missing from the results (an invented URL) does not count.
- A file:line citation counts **only when the result of a tool call that read that one file shows it with its line number**. That call is a read tool with a single path key (`line<TAB>content`), or the shell command `cat -n FILE`/`nl -ba FILE`/`grep -n PATTERN FILE` (one file, no pipe). The path must match exactly: no suffix match, only normalization against `cwd` and `workspace`. These do not count: lines printed with `echo`, `cat -n a b`, `grep -rn`, `wc -l`, search, stats or write tools given only a path key, top-level `tool_results`, and piped output. A line that does not exist does not count.
- A criterion the checks cannot confirm automatically comes out as needs-review. Even when every automatic check passes, the verdict is then REVIEW (exit code 3), not PASS.
- When `workspace` is set, any access to an absolute path outside it also violates `no-answer-lookup`.
- A malformed transcript fails the required `transcript-valid` check and yields a FAIL report. It does not crash with an exception.

The checks catch, all as required items: use of an undeclared tool, a missing expected tool, a missing scenario `must_include` keyword, answer lookup (`no-answer-lookup`: searching outside the workspace or digging through git history), and success_criteria or expected_outcome results that fall short (for example the number of verified source URLs, the number of file:line citations, zero changed files, the character count, or a comparison table).
