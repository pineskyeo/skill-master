---
name: csv-cleaner
description: Removes duplicate rows and trims whitespace in one CSV file. Use when a CSV has messy rows before analysis.
license: MIT
metadata:
  version: 1.0.0
---
# CSV Cleaner
Run `scripts/clean.py <in.csv> <out.csv>`.
## Inputs
- csv_path: 정리할 CSV 파일 경로
## Outputs
- cleaned_csv_path: 중복 행을 지우고 칸 앞뒤 공백을 없앤 CSV
## Example
입력: 중복 3줄이 있는 `a.csv` → 출력: 중복 제거된 `a.clean.csv`
