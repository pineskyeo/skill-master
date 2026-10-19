I couldn't find `survey_responses.csv`, so I haven't cleaned anything yet. The working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-scickkjh/work`) contains only the `.claude` skills folder. A broader filesystem search was not approved.

The csv-cleaner script is ready. It strips leading and trailing whitespace from every cell and drops exact duplicate rows, keeping the first occurrence. It takes an input path and an output path.

Two things to know before I run it:
- **Exact duplicates only.** Rows count as duplicates only if every column matches after trimming. A resubmission with a different timestamp or response ID would be kept, so tell me if duplicates should be matched on specific columns instead.
- **Output location.** I'll write to a new file (e.g. `survey_responses.clean.csv`) and leave the original untouched.

Can you give me the full path to `survey_responses.csv`, or copy it into the working directory?