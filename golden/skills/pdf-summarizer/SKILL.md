---
name: pdf-summarizer
description: Summarizes a single PDF document into a Korean one-page brief. Use when the user hands over a PDF and asks for a summary.
version: 1.0.0
inputs:
  - pdf_path (string) - PDF file path
outputs:
  - summary.md - one-page Korean summary
---
# PDF Summarizer
## Steps
1. `pdftotext`로 텍스트를 추출한다.
2. 섹션별 핵심을 3줄 이내로 정리한다. 형식은 [references/format.md](references/format.md)를 따른다.
## Example
입력: `report.pdf` → 출력: `summary.md` (제목, 핵심 3줄, 결론)
