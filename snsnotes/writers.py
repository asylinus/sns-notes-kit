"""Write per-post notes and NotebookLM bundles."""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

from .core import Entry
from .privacy import mask_mentions, redact, scrub_known

DEFAULT_MAX_CHARS = 400_000
VOICE_DIR = "1_내목소리"  # NotebookLM notebook 1: what I wrote
TASTE_DIR = "2_내취향"  # NotebookLM notebook 2: what I saved / liked
MAX_FILES = 45  # free NotebookLM: 50 sources per notebook, keep a little headroom
MAX_WORDS = 350_000  # free NotebookLM: 500k words per source
MAX_BYTES = 50 * 1024 * 1024  # free NotebookLM: 200MB per source
GROW = 1.5
HARD_CAP_CHARS = 200_000_000


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


def _span(chunk) -> str:
    """Label of the groups/years a file covers, e.g. 'instagram_2019-2021' or 'instagram_2021-threads_2022'."""
    (k1, y1), (k2, y2) = (chunk[0][0], chunk[0][1]), (chunk[-1][0], chunk[-1][1])
    if (k1, y1) == (k2, y2):
        return f"{k1}_{y1}"
    return f"{k1}_{y1}-{y2}" if k1 == k2 else f"{k1}_{y1}-{k2}_{y2}"


def _pack(items, max_chars, bounded, max_words, max_bytes):
    """items: [(group, year, block)] in order. Returns list of chunks (lists of items)."""
    chunks: list[list] = []
    cur: list = []
    size = words = nbytes = 0
    budget = max(max_chars - 200, 1)  # room for the header
    for it in items:
        b = it[2]
        w, nb = len(b.split()), len(b.encode("utf-8"))
        boundary = bounded and cur and (cur[-1][0], cur[-1][1]) != (it[0], it[1])
        if cur and (boundary or size + len(b) > budget or words + w > max_words or nbytes + nb > max_bytes):
            chunks.append(cur)
            cur, size, words, nbytes = [], 0, 0, 0
        cur.append(it)
        size += len(b)
        words += w
        nbytes += nb
    if cur:
        chunks.append(cur)
    return chunks


def pack_files(items, out: Path, title, note, max_chars=DEFAULT_MAX_CHARS, max_files=MAX_FILES,
               max_words=MAX_WORDS, max_bytes=MAX_BYTES, info=None) -> list[Path]:
    """Write items into <= max_files files.

    First keep years apart (same year -> same file when possible) and grow the size budget; if that is
    still too many files, let several years share a file. If even that exceeds max_files, write anyway
    and report over_limit in `info`.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.md"):  # stale files from a previous split would be uploaded by mistake
        old.unlink()
    items = sorted(items, key=lambda it: (it[0], it[1])) if items else []
    chosen, merged, used = None, False, max_chars
    for merged in (False, True):
        mc = max(max_chars, 1000)
        while True:
            chunks = _pack(items, mc, not merged, max_words, max_bytes)
            if len(chunks) <= max_files or mc >= HARD_CAP_CHARS:
                break
            mc = int(mc * GROW) + 1
        chosen, used = chunks, mc
        if len(chunks) <= max_files:
            break
    chunks = chosen or []
    files: list[Path] = []
    per_key: dict = defaultdict(int)
    for c in chunks:
        per_key[(c[0][0], c[0][1])] += 1
    seen: dict = defaultdict(int)
    for n, c in enumerate(chunks, 1):
        multi = len({(x[0], x[1]) for x in c}) > 1
        label = _span(c)
        if multi:
            seen_keys = list(dict.fromkeys(f"{x[0]}_{x[1]}" for x in c))
            header = ", ".join(seen_keys)
        else:
            header = label
        if multi:
            name = f"merged{n:02d}_{label}.md"
        else:
            k = (c[0][0], c[0][1])
            seen[k] += 1
            name = f"{label}.md" if per_key[k] == 1 else f"{label}_part{seen[k]}.md"
            if per_key[k] > 1:
                header += f" (part {seen[k]}/{per_key[k]})"
        body = f"# {title}: {header} - {len(c)} entries ({note})\n\n" + "".join(x[2] for x in c)
        p = out / name
        p.write_text(body, encoding="utf-8")
        files.append(p)
    if info is not None:
        info.update(
            files=len(files), over_limit=len(files) > max_files, merged_years=merged and any(
                len({(x[0], x[1]) for x in c}) > 1 for c in chunks),
            max_chars_used=used, max_files=max_files,
            max_words=max((sum(len(x[2].split()) for x in c) for c in chunks), default=0),
            max_bytes=max((p.stat().st_size for p in files), default=0),
        )
    return files


def write_bundle(entries: list[Entry], out: Path, do_redact=False, max_chars=DEFAULT_MAX_CHARS, account="", known=frozenset(),
                 max_files=MAX_FILES, info=None, max_words=MAX_WORDS, max_bytes=MAX_BYTES) -> list[Path]:
    items = [(e.source, e.when.year, _block(e, do_redact, account, known)) for e in entries if e.text]
    return pack_files(items, out, "My own posts", "own posts, original text", max_chars, max_files, max_words,
                      max_bytes, info)


def write_nlm(entries, recs, nlm: Path, do_redact=True, max_chars=DEFAULT_MAX_CHARS, account="", known=frozenset(),
              max_files=MAX_FILES, max_words=MAX_WORDS, max_bytes=MAX_BYTES) -> dict:
    """Write the two NotebookLM folders: nlm/1_내목소리 (own posts) and nlm/2_내취향 (saved/liked).

    Returns {"voice": info|None, "taste": info|None, "warnings": [...]}. A folder with nothing to write is
    not created.
    """
    from .taste import write_taste_bundle

    nlm = Path(nlm)
    res: dict = {"voice": None, "taste": None, "warnings": []}
    if any(e.text for e in entries):
        res["voice"] = {}
        write_bundle(entries, nlm / VOICE_DIR, do_redact, max_chars, account, known, max_files, res["voice"],
                     max_words, max_bytes)
    if recs and any(r.caption for r in recs):
        res["taste"] = {}
        write_taste_bundle(recs, nlm / TASTE_DIR, do_redact, max_chars, max_files, res["taste"], max_words, max_bytes)
    for key, folder in (("voice", VOICE_DIR), ("taste", TASTE_DIR)):
        i = res[key]
        if i and i["over_limit"]:
            res["warnings"].append(
                f"{folder}: {i['files']} files, more than the {max_files} that fit a free NotebookLM notebook. "
                "Use NotebookLM Plus or higher, or split this folder across more notebooks. / "
                f"{folder}: 파일이 {i['files']}개라 무료 노트북 한도({max_files}개)를 넘어요. "
                "Plus 이상이거나 노트북을 더 나누세요."
            )
    return res
