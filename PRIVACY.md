# Privacy

- **Everything runs on your computer.** snsnotes uses only the Python standard library and makes no network calls. It never uploads, sends, or logs your data anywhere.
- It reads your export zip directly and writes new files next to it. Your original zip is never modified.
- Your export contains personal data (your posts, and sometimes other people's names, phone numbers, emails). Treat the output folders like the original export.

## Before you upload anything (NotebookLM, ChatGPT, etc.)

1. Run `scan` first (`run_windows.bat` / `run_mac.command` already create `scan_report.md`). It tells you how many phone numbers, emails, bank-account-like numbers and @mentions of other people were found, and on which post dates, without printing the values.
2. The scan is a heuristic. It can miss things and can flag harmless numbers. It is a helper, not a guarantee.
3. Use `--redact` to mask phones, emails and account numbers in the generated notes and bundles. Names and @mentions are not masked: review those posts yourself.
4. Uploading a file to NotebookLM (or any cloud service) sends your text to that company. Check the service's current terms on data use and retention before choosing what to upload. Upload only the years or sources you are comfortable sharing, and prefer redacted bundles.
5. Posts that mention other people (friends, family, customers) are their data too. When in doubt, leave them out.
6. Delete the generated folders when you no longer need them.
