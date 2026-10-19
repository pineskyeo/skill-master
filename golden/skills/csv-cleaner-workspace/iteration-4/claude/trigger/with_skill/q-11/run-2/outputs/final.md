I couldn't convert `report.xlsx` to CSV, and nothing was created.

- **Shell access:** Bash only allows one command here, running the installed `clean.py` with two paths. My first two shell commands were rejected for that reason, so I can't run Python, `libreoffice` or `ssconvert` to do the conversion.
- **Reading the file:** The Read tool can't open `.xlsx` files, because they're binary.
- **The `csv-cleaner` skill:** It removes duplicate rows and trims whitespace in a CSV. It doesn't read Excel files, so it doesn't help with this conversion. I didn't use it, since it would run on a file that isn't a CSV.

You have two options:
1. **Lift the Bash restriction:** Then I can run something like `python3 -I -c "import openpyxl, csv, sys; ..." report.xlsx report.csv`. I can also use `libreoffice --headless --convert-to csv`.
2. **Convert it yourself:** In Excel, use File → Save As → CSV. Or run `libreoffice --headless --convert-to csv report.xlsx`.

If the workbook has several sheets, tell me which one you want. CSV holds a single sheet.