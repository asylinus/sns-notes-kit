from __future__ import annotations

import argparse
import zipfile
import sys
from collections import defaultdict
from pathlib import Path

from . import __version__
from .core import HTML_MSG, ExportError, is_html_export, parse_tz, read_export
from .taste import has_taste_files, known_usernames, read_taste, taste_stats, write_taste
from .privacy import scan_text
from .writers import DEFAULT_MAX_CHARS, TASTE_DIR, VOICE_DIR, write_bundle, write_nlm, write_notes

LABELS = {
    "phone": "Phone numbers",
    "email": "Email addresses",
    "account": "Bank-account-like numbers",
    "mention": "@mentions of other people",
}


def build_report(entries, account="", taste=None, nlm=None) -> str:
    totals = dict.fromkeys(LABELS, 0)
    dates = defaultdict(list)
    for e in entries:
        c = scan_text(e.text, account)
        for k, n in c.items():
            if n:
                totals[k] += n
                dates[k].append(f"{e.when:%Y-%m-%d %H:%M} ({e.source})")
    lines = [
        "# Privacy pre-check (values are NOT shown)",
        "",
        f"Scanned {len(entries)} posts. Patterns are heuristic: it can miss things and flag harmless numbers.",
        "",
    ]
    for k, label in LABELS.items():
        lines.append(f"## {label}: {totals[k]}")
        lines += [f"- {d}" for d in dates[k]] or ["- none"]
        lines.append("")
    if taste is not None:
        st = taste_stats(taste)
        lines.append(f"## Taste (saved / liked): {st['total']} entries, {st['collections']} collections")
        lines += [f"- {k}: {n}" for k, n in sorted(st["kinds"].items())] or ["- none"]
        lines.append("- by year: " + (", ".join(f"{y}: {n}" for y, n in st["years"].items()) or "none"))
        lines.append("")
    if nlm:
        lines.append("## NotebookLM folders")
        for key, folder in (("voice", VOICE_DIR), ("taste", TASTE_DIR)):
            i = nlm.get(key)
            if i:
                lines.append(
                    f"- nlm/{folder}: {i['files']} files (largest: {i['max_words']:,} words, {i['max_bytes'] / 1048576:.1f} MB)"
                    + (" - several years share a file (file headers show the years)" if i["merged_years"] else "")
                )
        lines += [f"- WARNING: {w}" for w in nlm["warnings"]]
        lines.append("")
    lines.append("Note: phones/emails/accounts are masked by default in notes, bundles and taste notes (use --no-redact to turn off).")
    return "\n".join(lines) + "\n"


def _common(p, out_required=True, out_help="output folder"):
    p.add_argument("zip", help="Meta export .zip (JSON format)")
    p.add_argument("-o", "--out", required=out_required, help=out_help)
    p.add_argument("--tz", default="local", help="timezone: local (default), KST, +09:00, Asia/Seoul")
    p.add_argument("--account", default="", help="your own handle (excluded from @mention count; written to notes)")


def _redact_flag(p):
    p.add_argument("--no-redact", action="store_true", help="do NOT mask phones/emails/account numbers/@mentions (masking is on by default)")
    p.add_argument("--redact", action="store_true", help=argparse.SUPPRESS)  # kept for old scripts (now the default)


def _notes_flag(p):
    p.add_argument("--notes", action="store_true", help="also write one note per post/saved item (notes/, taste/) for Obsidian-like apps (tens of thousands of files)")


def parser():
    ap = argparse.ArgumentParser(
        prog="snsnotes",
        description="Instagram/Threads export to notes and NotebookLM bundles (runs locally, no network).",
    )
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("notes", help="one markdown note per post")
    _common(p)
    _redact_flag(p)
    p = sub.add_parser("bundle", help="NotebookLM-friendly yearly files")
    _common(p)
    _redact_flag(p)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="start size per file (grows automatically to stay within 45 files)")
    p = sub.add_parser("wizard", help="step-by-step browser wizard (local only, closes with the tab)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    p.add_argument("--port", type=int, default=0, help="fixed port (default: random free port)")
    p.add_argument("--zip", default="", help="pre-select this export zip")
    p = sub.add_parser("taste", help="experimental: NotebookLM files for saved posts, likes and collections (other people's names are hashed)")
    _common(p)
    _redact_flag(p)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    _notes_flag(p)
    p = sub.add_parser("scan", help="privacy pre-check report (no values)")
    _common(p, out_required=False, out_help="also save report to this file")
    p.add_argument("--taste", action="store_true", help="experimental: also count saved/liked")
    p = sub.add_parser("all", help="NotebookLM folders (my voice + my taste) + scan report; --notes adds one-file-per-post notes")
    _common(p, out_required=False, out_help="output folder (default: next to the zip)")
    _redact_flag(p)
    p.add_argument("--taste", action="store_true", help="experimental: also build saved/liked/collections files (2_내취향)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    _notes_flag(p)
    return ap


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.version_info < (3, 10):
        print("Error: Python 3.10 or newer is required. Install it from https://www.python.org/downloads/", file=sys.stderr)
        return 2
    a = parser().parse_args(argv)
    try:
        return _run(a)
    except ExportError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(
            f"Error: could not write files ({e.strerror or e}). Check that the output folder is writable "
            "and the disk is not full. If your zip is in a read-only place, use -o <folder>.",
            file=sys.stderr,
        )
        return 2


def _zip_names(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return []


def _taste(a, strict=False):
    """Read taste records. strict=True (taste command): a zip without saved/liked files is an error."""
    if not has_taste_files(a.zip):
        if strict:
            if is_html_export(_zip_names(a.zip)):
                raise ExportError(HTML_MSG)
            raise ExportError("No saved/liked files found (saved_posts.json, liked_posts.json ...). Was this a JSON export that includes 'Saved' and 'Likes'?")
        return None
    recs, warns = read_taste(a.zip, parse_tz(a.tz))
    for w in warns:
        print(f"Warning: {w}", file=sys.stderr)
    return recs


def _run(a) -> int:
    if a.cmd == "wizard":
        from .wizard import serve

        return serve(port=a.port, open_browser=not a.no_browser, zip_path=a.zip)
    redact = not getattr(a, "no_redact", False)
    use_taste = a.cmd == "taste" or (a.cmd in ("all", "scan") and getattr(a, "taste", False))
    if a.cmd == "taste":
        base = Path(a.out)
        recs = _taste(a, strict=True)
        if a.notes:
            w, sk = write_taste(recs, base / "taste", redact)
            print(f"taste notes: {w} written, {sk} skipped (already exist) -> {base / 'taste'}")
        res = write_nlm([], recs, base / "nlm", redact, a.max_chars)
        _print_nlm(res, base / "nlm")
        return 0
    try:
        entries, warnings = read_export(a.zip, parse_tz(a.tz))
    except ExportError as e:
        if use_taste and has_taste_files(a.zip) and "does not look like" in str(e):
            entries, warnings = [], []  # saved/liked-only export
        else:
            print(f"Error: {e}", file=sys.stderr)
            return 2
    for w in warnings:
        print(f"Warning: {w}", file=sys.stderr)
    recs = _taste(a) if use_taste else None

    if a.cmd == "scan":
        report = build_report(entries, a.account, recs)
        print(report)
        if a.out:
            Path(a.out).write_text(report, encoding="utf-8")
        return 0

    known = known_usernames(recs) if (recs and redact) else frozenset()
    base = Path(a.out) if a.out else Path(a.zip).with_name(Path(a.zip).stem + "_snsnotes")
    if a.cmd == "notes" or (a.cmd == "all" and a.notes):
        dest = base / "notes" if a.cmd == "all" else base
        w, s = write_notes(entries, dest, redact, a.account, known)
        print(f"notes: {w} written, {s} skipped (already exist) -> {dest}")
    if a.cmd == "bundle":
        info = {}
        files = write_bundle(entries, base / VOICE_DIR, redact, a.max_chars, a.account, known, info=info)
        print(f"bundle: {len(files)} files -> {base / VOICE_DIR}")
    if a.cmd == "all":
        if recs is not None and a.notes:
            w, sk = write_taste(recs, base / "taste", redact)
            print(f"taste notes: {w} written, {sk} skipped (already exist) -> {base / 'taste'}")
        res = write_nlm(entries, recs, base / "nlm", redact, a.max_chars, a.account, known)
        _print_nlm(res, base / "nlm")
        base.mkdir(parents=True, exist_ok=True)
        (base / "scan_report.md").write_text(build_report(entries, a.account, recs, res), encoding="utf-8")
        print(f"scan report -> {base / 'scan_report.md'}")
    return 0


def _print_nlm(res, nlm):
    for key, folder in (("voice", VOICE_DIR), ("taste", TASTE_DIR)):
        i = res[key]
        if i:
            print(f"NotebookLM {folder}: {i['files']} files -> {nlm / folder}")
    for w in res["warnings"]:
        print(f"Warning: {w}", file=sys.stderr)
