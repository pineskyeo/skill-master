# korean-proofreader requirements

## What it does
- Takes one Korean sentence or document as `text` and corrects its spelling and spacing.
- Writes the corrected text to `corrected.txt` in the work folder. The answer also shows it, with `changes`, the list of changed parts.
- `changes` lists each spacing fix and each spelling fix (SKILL.md Example).
- Follows the spelling standard of the National Institute of Korean Language. The correct form for cases 1 and 2 is in `evals/files/short_phrase.expected.txt` and `evals/files/report_draft.expected.txt`. In case 3 it is `evals/files/weather_note.txt`, unchanged.
- Fixes only spacing and spelling. It leaves the content and the meaning unchanged (case 2).
- When nothing needs fixing, it says so, returns the original text unchanged, and leaves `changes` empty (case 3).

## When to use
- A person asks to proofread Korean text, or to fix its spelling or spacing.
- The text is a sentence, a paragraph, a document file, a cover letter, a report, an essay, a blog post, an email, or a product description.

## When not to use
- Proofreading English text.
- Checking Japanese or Chinese grammar.
- Translating Korean text into English.
- Summarizing a Korean article or a PDF.
- Rewriting text in a more polite tone.
- Asking how a word is pronounced, or how to romanize a name.
- Writing new Korean text.

## First live run
- Engine under test: Claude Code (`claude`), model `claude-sonnet-5-5`, `--effort high`, runs 1, with_skill against without_skill.
- Grading and the blind pairwise comparison: Codex (CLI default model), reasoning effort high, `--sandbox read-only`. Codex is not under test.
- This is the setup of csv-cleaner, so the three golden skills compare on one basis. pinesky named it, so it replaces the `unit-requirements` default, which is `claude` and `codex` with 3 runs.
- `DEFAULT_RUNS` in `policy/eval-policy.json` is 3. The golden set uses runs 1 as a deliberate choice.
- `evals/evals.json` has 3 cases and 8 items. 5 are static (3 `file_equals_fixture`, 2 `not_regex`) and 3 are llm strings.
- Input files: `evals/files/short_phrase.txt` (case 1), `evals/files/report_draft.txt` (case 2), and `evals/files/weather_note.txt` (case 3).
- Fixtures: `evals/files/short_phrase.expected.txt` (case 1) and `evals/files/report_draft.expected.txt` (case 2). Case 3 compares `corrected.txt` with its input file.
- The trigger evals in `evals/trigger_evals.json` have 10 should-use queries and 10 should-not queries.
- Limit: the fixtures are in the work folder, because a `file_equals_fixture` check needs its fixture in the case's `files`. The `not_regex` check fails a run whose tool input or answer names a fixture. It does not stop a wildcard copy.

## Pass criteria
- The with_skill mean pass_rate is not lower than the without_skill mean pass_rate.
- In the with_skill trigger evals, the 10 should-use queries have a trigger_rate at or above 0.5, and the 10 should-not queries have a trigger_rate below 0.5.
- No with_skill run fails any of the 5 static items.
- No run has an execution error, an isolation violation, or an ungraded item. All 3 llm items are graded.
- Case 1: `corrected.txt` equals `evals/files/short_phrase.expected.txt` byte for byte.
- Case 1: the transcript has no match for `\.expected\.txt`.
- Case 1: `changes` lists one spacing fix and one spelling fix.
- Case 2: `corrected.txt` equals `evals/files/report_draft.expected.txt` byte for byte.
- Case 2: the transcript has no match for `\.expected\.txt`.
- Case 2: the sentence does not change in meaning.
- Case 3: `changes` is empty.
- Case 3: `corrected.txt` equals `evals/files/weather_note.txt` byte for byte.

## Open questions
- None.
