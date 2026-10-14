---
name: csv-cleaner
description: Removes duplicate rows and trims whitespace in one CSV file. Use when a CSV has messy rows before analysis.
license: MIT
metadata:
  version: 1.0.0
inputs: [csv_path]
outputs: [cleaned_csv_path]
---
# CSV Cleaner
Run `scripts/clean.py <in.csv> <out.csv>`.
## Example
입력: 중복 3줄이 있는 `a.csv` → 출력: 중복 제거된 `a.clean.csv`
