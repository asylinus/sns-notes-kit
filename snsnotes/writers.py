"""Write per-post notes and NotebookLM bundles."""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

from .core import Entry
from .privacy import mask_mentions, redact, scrub_known

DEFAULT_MAX_CHARS = 400_000


def _q(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def write_notes(entries: list[Entry], out: Path, do_redact=False, account="", known=frozenset()) -> tuple[int, int]:
    """Returns (written, skipped). Existing notes with the same sha8 are skipped."""
    out = Path(out)
    existing = {p.stem.rsplit("_", 1)[-1] for p in out.rglob("*.md")} if out.exists() else set()
    written = skipped = 0
    for e in entries:
        if e.sha8 in existing:
            skipped += 1
            continue
        text = scrub_known(mask_mentions(redact(e.text), account), known) if do_redact else e.text
        fm = ["---", f"source: {_q(e.source)}"]
        if account:
            fm.append(f"account: {_q(account)}")
        fm += [
            f"kind: {_q(e.kind)}",
            f"created: {_q(e.when.isoformat(timespec='seconds'))}",
            f"media_count: {len(e.media)}",
            f"media_paths: {_q(e.media)}",
            f"zip_file: {_q(e.zip_name)}",
            f"sha8: {_q(e.sha8)}",
            "---",
        ]
        d = out / e.source / str(e.when.year)
        d.mkdir(parents=True, exist_ok=True)
        name = f"{e.when:%Y-%m-%d_%H%M}_{e.sha8}.md"
        (d / name).write_text("\n".join(fm) + "\n\n" + (text or "(no text)") + "\n", encoding="utf-8")
        existing.add(e.sha8)
        written += 1
    return written, skipped


def _block(e: Entry, do_redact: bool, account: str = "", known=frozenset()) -> str:
    media = f" [media {len(e.media)}]" if e.media else ""
    text = scrub_known(mask_mentions(redact(e.text), account), known) if do_redact else e.text
    return f"## {e.when:%Y-%m-%d %H:%M} | {e.kind}{media}\n{text}\n\n"


def write_bundle(entries: list[Entry], out: Path, do_redact=False, max_chars=DEFAULT_MAX_CHARS, account="", known=frozenset()) -> list[Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, int], list[Entry]] = defaultdict(list)
    for e in entries:
        if e.text:  # nothing for NotebookLM to read in media-only posts
            groups[(e.source, e.when.year)].append(e)
    files: list[Path] = []
    for (source, year), es in sorted(groups.items()):
        parts: list[list[str]] = [[]]
        size = 0
        budget = max_chars - 200  # room for header
        for e in es:
            b = _block(e, do_redact, account, known)
            if parts[-1] and size + len(b) > budget:
                parts.append([])
                size = 0
            parts[-1].append(b)
            size += len(b)
        for i, blocks in enumerate(parts, 1):
            label = f"{source.capitalize()} {year}" + (f" (part {i}/{len(parts)})" if len(parts) > 1 else "")
            body = f"# {label} - {len(blocks)} entries (own posts, original text)\n\n" + "".join(blocks)
            suffix = f"_part{i}" if len(parts) > 1 else ""
            p = out / f"{source}_{year}{suffix}.md"
            p.write_text(body, encoding="utf-8")
            files.append(p)
    return files
