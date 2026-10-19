I couldn't convert `report.xlsx` to CSV, and no CSV file was created.

- **Shell:** Bash only allows one command here, the installed `clean.py` invocation with two paths. That blocks the `python3`/openpyxl conversion I would normally use. It also blocked a plain `ls`.
- **Read tool:** It refuses binary files, and an `.xlsx` is a zipped XML package.
- **`csv-cleaner` skill:** It only removes duplicate rows and trims whitespace in a CSV. It doesn't read Excel files, so I didn't use it.

Any of these would let me finish:
1. Allow general Bash, or at least `python3 -I` with a script. Then I'd convert it with openpyxl or pandas, one CSV per sheet if there are several.
2. Export it yourself from Excel (File → Save As → CSV). I can then clean it with `csv-cleaner` if you want.
3. Tell me which sheet you need and paste its contents. I can write the CSV from that.