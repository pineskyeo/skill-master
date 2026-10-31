---
name: pdf-summarizer
description: Summarizes a single PDF document into a Korean one-page brief. Use when the user hands over a PDF and asks for a summary.
license: MIT
metadata:
  version: 1.0.0
---
# PDF Summarizer
## Inputs
- pdf_path: path of the PDF file to summarize
## Outputs
- summary.md: a one-page summary in Korean
## Steps
1. Extract the text with `pdftotext`.
2. Reduce the key points of each section to at most three lines. Write the summary in Korean, whatever the language of the PDF. Follow [references/format.md](references/format.md) for the format.
## Example
Input: `report.pdf`. Output: `summary.md` (title, three key lines, conclusion).
