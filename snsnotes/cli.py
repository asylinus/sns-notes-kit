from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from . import __version__
from .core import ExportError, parse_tz, read_export
from .privacy import scan_text
from .writers import DEFAULT_MAX_CHARS, write_bundle, write_notes

LABELS = {
    "phone": "Phone numbers",
    "email": "Email addresses",
    "account": "Bank-account-like numbers",
    "mention": "@mentions of other people",
}


def build_report(entries, account="") -> str:
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
    lines.append("Tip: re-run with --redact to mask phones/emails/accounts in notes and bundles.")
    return "\n".join(lines) + "\n"


def _common(p, out_required=True, out_help="output folder"):
    p.add_argument("zip", help="Meta export .zip (JSON format)")
    p.add_argument("-o", "--out", required=out_required, help=out_help)
    p.add_argument("--tz", default="local", help="timezone: local (default), KST, +09:00, Asia/Seoul")
    p.add_argument("--account", default="", help="your own handle (excluded from @mention count; written to notes)")


def _redact_flag(p):
    p.add_argument("--redact", action="store_true", help="mask phones/emails/account numbers")


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
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="max chars per file")
    p = sub.add_parser("scan", help="privacy pre-check report (no values)")
    _common(p, out_required=False, out_help="also save report to this file")
    p = sub.add_parser("all", help="notes + bundle + scan report")
    _common(p, out_required=False, out_help="output folder (default: next to the zip)")
    _redact_flag(p)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
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


def _run(a) -> int:
    try:
        entries, warnings = read_export(a.zip, parse_tz(a.tz))
    except ExportError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    for w in warnings:
        print(f"Warning: {w}", file=sys.stderr)
    redact = getattr(a, "redact", False)

    if a.cmd == "scan":
        report = build_report(entries, a.account)
        print(report)
        if a.out:
            Path(a.out).write_text(report, encoding="utf-8")
        return 0

    base = Path(a.out) if a.out else Path(a.zip).with_name(Path(a.zip).stem + "_snsnotes")
    if a.cmd in ("notes", "all"):
        dest = base / "notes" if a.cmd == "all" else base
        w, s = write_notes(entries, dest, redact, a.account)
        print(f"notes: {w} written, {s} skipped (already exist) -> {dest}")
    if a.cmd in ("bundle", "all"):
        dest = base / "nlm" if a.cmd == "all" else base
        files = write_bundle(entries, dest, redact, a.max_chars)
        print(f"bundle: {len(files)} files -> {dest}")
    if a.cmd == "all":
        base.mkdir(parents=True, exist_ok=True)
        (base / "scan_report.md").write_text(build_report(entries, a.account), encoding="utf-8")
        print(f"scan report -> {base / 'scan_report.md'}")
    return 0
