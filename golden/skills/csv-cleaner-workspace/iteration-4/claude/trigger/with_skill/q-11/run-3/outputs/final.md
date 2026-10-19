I haven't converted `report.xlsx`. I can't do it with the tools I have in this session.

- **Shell:** Bash only allows one command here, an installed `clean.py` called with two paths. Both of my attempts (listing the directory and checking for Python's `openpyxl` library) were rejected.
- **File reader:** `report.xlsx` is a binary file, and the Read tool won't open those.
- **Skill:** The only skill available is `csv-cleaner`. It removes duplicate rows and trims whitespace in a CSV file. It doesn't read `.xlsx`, so I haven't used it for the conversion.

I didn't try to get around the Bash restriction.

Any of these would let me finish:
1. You allow general shell commands. I'd then convert it with Python (`openpyxl` or `pandas`), or with LibreOffice if it's installed.
2. You export it yourself from Excel, Numbers or Google Sheets (File → Save As / Download → CSV). I can then clean it up with `csv-cleaner` if you want.
3. You run this yourself, which needs `pandas` and `openpyxl`: `python3 -c "import pandas as pd; pd.read_excel('report.xlsx').to_csv('report.csv', index=False)"`

If the workbook has more than one sheet, say which sheet you want. A CSV can only hold one.