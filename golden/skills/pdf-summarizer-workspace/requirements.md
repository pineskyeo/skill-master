# pdf-summarizer requirements

## What it does
- Takes one PDF file as `pdf_path` and writes `summary.md`, a one-page summary in Korean, whatever the language of the PDF.
- Extracts the text with `pdftotext`.
- Follows `references/format.md` and the SKILL.md Example: a title, three key lines, and a one-line conclusion.
- When a PDF holds only scanned images and the `pdftotext` output is empty, it says that the text extraction failed and does not make up a summary (case 3).
- Leaves the PDF unchanged.

## When to use
- A person hands over one PDF and asks for a summary.
- The request is for a one-page summary, a Korean summary, the key points, a short summary, or a Korean summary of an English PDF.

## When not to use
- Summarizing a docx file, a txt file, or a web article.
- Translating a PDF page by page.
- Extracting a PDF table to CSV.
- Editing the content of a PDF.
- Merging or splitting PDFs.
- Running OCR on an image.
- Writing a report and exporting it to PDF.

## First live run
- Engine under test: Claude Code (`claude`), model `claude-sonnet-5-5`, `--effort high`, runs 1, with_skill against without_skill.
- Grading and the blind pairwise comparison: Codex (CLI default model), reasoning effort high, `--sandbox read-only`. Codex is not under test.
- This is the setup of csv-cleaner, so the three golden skills compare on one basis. pinesky named it, so it replaces the `unit-requirements` default, which is `claude` and `codex` with 3 runs.
- `DEFAULT_RUNS` in `policy/eval-policy.json` is 3. The golden set uses runs 1 as a deliberate choice.
- `pdftotext` is a required dependency. It is confirmed before a live run, and the run does not start without it.
- `evals/evals.json` has 3 cases and 5 items. All 5 are llm strings, and none is static.
- Input files: one small synthetic PDF per case, listed in that case's `files`. No case uses an external document.
- Case 1 reads `evals/files/report.pdf`, and case 2 reads `evals/files/2025_business_plan_v3.pdf`, which is in English.
- Case 3 reads `evals/files/scanned_notice.pdf`, one image page with no text layer.
- The trigger evals in `evals/trigger_evals.json` have 10 should-use queries and 10 should-not queries.

## Pass criteria
- The with_skill mean pass_rate is not lower than the without_skill mean pass_rate. Only the 5 llm items set pass_rate.
- In the with_skill trigger evals, the 10 should-use queries have a trigger_rate at or above 0.5, and the 10 should-not queries have a trigger_rate below 0.5.
- No with_skill run fails a static item. This skill has no static item, so this gate checks nothing here and cannot fail.
- No run has an execution error, an isolation violation, or an ungraded item. All 5 llm items need `grade --live`.
- Case 1: `summary.md` has a title, three key lines, and a one-line conclusion.
- Case 2: the summary is written in Korean.
- Case 2: the summary follows the one-page format of three key lines and a conclusion.
- Case 3: the answer tells the user that the text extraction failed.
- Case 3: the answer does not make up a summary with content that is not in the original.

## Open questions
- None.
