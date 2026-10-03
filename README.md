# snsnotes

Turn **your own** Instagram / Threads data export into:

- one Markdown note per post (frontmatter + original text), and
- NotebookLM-ready bundles (one file per source per year, auto-split under ~400k characters),
- plus a privacy pre-check report (no values shown).

Python 3.10+, standard library only, **runs fully offline**. See [PRIVACY.md](PRIVACY.md). Korean guide: [README_ko.md](README_ko.md).

## 1. Get your export

Instagram app/web: Accounts Center > Your information and permissions > Download your information > choose profile > **Posts** (and **Threads** if you use it) > date range *All time* > format **JSON** (not HTML) > submit. Meta emails you a download link (can take hours to days). Menu names change over time.

![screenshot placeholder: request export](docs/img/01_request.png)

## 2. Run it (no install needed)

- **Windows**: drag the zip onto `run_windows.bat`.
- **macOS**: drag the zip onto `run_mac.command` (first time: `chmod +x run_mac.command`).

Results appear next to the zip in `<zipname>_snsnotes/`: `notes/`, `nlm/`, `scan_report.md`.

## 3. Command line

```
python -m snsnotes scan   export.zip [-o report.md]
python -m snsnotes notes  export.zip -o out/   [--redact]
python -m snsnotes bundle export.zip -o nlm/   [--redact] [--max-chars 400000]
python -m snsnotes all    export.zip [-o dir]  [--redact]
```

Common options: `--tz local|KST|+09:00|Asia/Seoul` (default: your computer's timezone; `Asia/Seoul` on Windows needs `pip install tzdata`), `--account HANDLE` (your handle: excluded from the @mention count, written to note frontmatter).

Optional install: `pip install .` gives you a `snsnotes` command.

### Output

```
out/instagram/2024/2024-03-01_1530_ab12cd34.md   # source/year/date_time_sha8.md
nlm/instagram_2024.md  nlm/instagram_2024_part2.md  nlm/threads_2024.md
```

Re-running `notes` skips posts whose sha8 already exists. Missing files in the export (e.g. no Threads) produce a warning, not a crash.

## Tests

```
python -m unittest discover -s tests -v
```

Tests use a synthetic fake export (`tests/fake_export.py`); no real data is included in this repo.

## Limits

Reads feed posts and Threads text posts only (not stories, reels metadata, DMs, comments). Export layouts can change; if your zip is not recognized, open an issue with the *file list* (never the content).

## License

MIT (see LICENSE).
