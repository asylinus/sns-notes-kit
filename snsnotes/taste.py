"""Taste notes: saved posts, likes and collections from a Meta export zip.

Privacy by design: other people's account names are never stored, only a short hash (owner_hash).
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .core import MAX_RATIO, fix_mojibake
from .privacy import MENTION_ANY, redact as redact_contacts, scrub_known
from .stream import iter_array

MAX_TASTE_BYTES = 8 * 1024 ** 3  # streamed, so big files are fine; refuse absurd sizes only
BIG_WARN_BYTES = 500 * 1024 * 1024

FILES = {
    "saved_posts": re.compile(r"(^|/)saved/saved_posts(_\d+)?\.json$"),
    "saved_collections": re.compile(r"(^|/)saved/saved_collections(_\d+)?\.json$"),
    "saved_music": re.compile(r"(^|/)saved/saved_music(_\d+)?\.json$"),
    "liked_posts": re.compile(r"(^|/)likes/liked_posts(_\d+)?\.json$"),
    "liked_comments": re.compile(r"(^|/)likes/liked_comments(_\d+)?\.json$"),
    "liked_threads": re.compile(r"(^|/)threads/liked_threads(_\d+)?\.json$"),
}
KINDS = ("saved", "liked", "saved+liked", "liked_thread", "liked_comment", "saved_music")

_IG_URL = re.compile(r"instagram\.com/(?:[^/?#]+/)?(p|reel|reels|tv)/([\w-]+)(?:/c/(\d+))?", re.I)
_TH_URL = re.compile(r"(threads\.(?:net|com))/@([^/?#]+)/post/([\w-]+)", re.I)
_IG_PROFILE_IN_TEXT = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?instagram\.com/(?!(?:p|reel|reels|tv|explore|stories)/)[\w.]+/?"
)


@dataclass
class Taste:
    kind: str
    key: str
    caption: str = ""
    url: str = ""
    hashtags: list = field(default_factory=list)
    owner_hash: str = ""
    owner_tokens: tuple = ()  # never written out; only used to scrub captions
    owner_user: str = ""  # never written out; lowercase username if known
    collections: set = field(default_factory=set)
    saved_at: dt.datetime | None = None
    liked_at: dt.datetime | None = None

    @property
    def when(self):
        return self.saved_at or self.liked_at

    @property
    def sha8(self) -> str:
        return hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:8]


def owner_hash(name: str) -> str:
    n = (name or "").strip().lower().lstrip("@")
    return hashlib.sha1(n.encode("utf-8")).hexdigest()[:10] if n else ""


def clean_url(u: str) -> tuple[str, str, str]:
    """Return (public url without account names, merge key, owner username or '')."""
    u = u or ""
    m = _IG_URL.search(u)
    if m:
        kind = {"reels": "reel"}.get(m.group(1).lower(), m.group(1).lower())
        url = f"https://www.instagram.com/{kind}/{m.group(2)}/" + (f"c/{m.group(3)}/" if m.group(3) else "")
        return url, f"ig:{kind}/{m.group(2)}" + (f"/c/{m.group(3)}" if m.group(3) else ""), ""
    m = _TH_URL.search(u)
    if m:
        return f"https://www.{m.group(1).lower()}/@someone/post/{m.group(3)}", f"th:{m.group(3)}", m.group(2)
    base = u.split("?")[0]
    return base, f"u:{base}", ""


def _ts(v, tz):
    try:
        return dt.datetime.fromtimestamp(int(v), tz) if v else None
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def _vals(items):
    """label_values list -> (plain labels by name, dict groups by title)."""
    plain, groups = {}, {}
    for x in items or []:
        if not isinstance(x, dict):
            continue
        if "label" in x:
            plain.setdefault(x["label"], x)
        elif "dict" in x:
            groups.setdefault(x.get("title") or "", x["dict"])
    return plain, groups


def _group_labels(group):
    out = {}
    for g in group or []:
        for y in (g.get("dict") if isinstance(g, dict) else None) or []:
            if isinstance(y, dict) and "label" in y:
                out.setdefault(y["label"], y.get("value", ""))
    return out


def _group_names(group):
    names = []
    for g in group or []:
        for y in (g.get("dict") if isinstance(g, dict) else None) or []:
            if isinstance(y, dict) and y.get("label") == "Name" and y.get("value"):
                names.append(fix_mojibake(str(y["value"])))
    return names


def parse_labels(items, kind, ts=None) -> Taste | None:
    plain, groups = _vals(items)
    u = plain.get("URL") or {}
    href = u.get("href") or u.get("value") or ""
    url, key, _ = clean_url(href)
    cap = fix_mojibake(str((plain.get("Caption") or {}).get("value", ""))).strip()
    owner = _group_labels(groups.get("Owner"))
    uname = fix_mojibake(str(owner.get("Username", "")))
    oname = fix_mojibake(str(owner.get("Name", "")))
    if not href and not cap:
        return None
    if not href:
        key = "c:" + hashlib.sha256(f"{ts}|{cap}".encode("utf-8")).hexdigest()
    return Taste(
        kind=kind, key=key, caption=cap, url=url, hashtags=_group_names(groups.get("Hashtags")),
        owner_hash=owner_hash(uname or oname),
        owner_tokens=tuple(x for x in {uname, oname} if len(x) >= 3),
        owner_user=uname.strip().lower().lstrip("@"),
    )


def _parse_old(it, kind, tz):
    """Link-only shapes: string_list_data (likes, threads, comments) / string_map_data (older saved)."""
    sld = it.get("string_list_data")
    if sld is None and isinstance(it.get("string_map_data"), dict):
        sld = [v for v in it["string_map_data"].values() if isinstance(v, dict)]
    sld = [x for x in (sld or []) if isinstance(x, dict)]
    if not sld:
        return None
    href = sld[0].get("href") or ""
    url, key, uname = clean_url(href)
    who = fix_mojibake(str(it.get("title") or "")) or uname
    t = Taste(
        kind=kind, key=key, url=url, owner_hash=owner_hash(uname or who),
        owner_tokens=tuple(x for x in {uname, who} if len(x) >= 3),
        owner_user=(uname or (who if " " not in who else "")).strip().lower().lstrip("@"),
    )
    return t, _ts(sld[0].get("timestamp"), tz)


def _check(zf, n, warnings):
    info = zf.getinfo(n)
    if info.file_size > MAX_TASTE_BYTES or (info.compress_size and info.file_size / info.compress_size > MAX_RATIO):
        warnings.append(f"Skipped {n}: unusually large or highly compressed (possible zip bomb).")
        return False
    if info.file_size > BIG_WARN_BYTES:
        warnings.append(
            f"{n} is large ({info.file_size // 1048576} MB); it is read in a streaming way and may take a few minutes."
        )
    return True


def has_taste_files(zip_path) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return any(rx.search(n) for n in zf.namelist() for rx in FILES.values())
    except (OSError, zipfile.BadZipFile):
        return False


def read_taste(zip_path, tz) -> tuple[list[Taste], list[str]]:
    warnings: list[str] = []
    recs: dict[str, Taste] = {}

    def add(t: Taste, saved=None, liked=None, coll=None):
        cur = recs.get(t.key)
        if cur is None:
            cur = recs[t.key] = t
        else:
            if not cur.caption and t.caption:
                cur.caption = t.caption
            if not cur.hashtags and t.hashtags:
                cur.hashtags = t.hashtags
            if not cur.owner_hash and t.owner_hash:
                cur.owner_hash, cur.owner_tokens = t.owner_hash, t.owner_tokens
        if saved and (cur.saved_at is None or saved < cur.saved_at):
            cur.saved_at = saved
        if liked and (cur.liked_at is None or liked < cur.liked_at):
            cur.liked_at = liked
        if coll:
            cur.collections.add(coll)
        if cur.kind in ("saved", "liked", "saved+liked"):
            cur.kind = "saved+liked" if (cur.saved_at and cur.liked_at) else ("saved" if cur.saved_at else "liked")

    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
        found = {k: [n for n in names if rx.search(n)] for k, rx in FILES.items()}

        def stream(group):
            for n in found[group]:
                if not _check(zf, n, warnings):
                    continue
                try:
                    with zf.open(n) as f:
                        yield from iter_array(f)
                except (ValueError, KeyError, RuntimeError, zipfile.BadZipFile) as e:
                    warnings.append(f"Could not fully read {n}: {e}")

        for group, kind in (("saved_posts", "saved"), ("liked_posts", "liked")):
            for it in stream(group):
                if not isinstance(it, dict):
                    continue
                if "label_values" in it:
                    t = parse_labels(it["label_values"], kind, it.get("timestamp"))
                    when = _ts(it.get("timestamp"), tz)
                else:
                    old = _parse_old(it, kind, tz)
                    t, when = old if old else (None, None)
                if t:
                    add(t, saved=when if kind == "saved" else None, liked=when if kind == "liked" else None)

        cname = None
        fallback = {}  # collection time, used only for posts missing from saved_posts
        for it in stream("saved_collections"):
            if not isinstance(it, dict):
                continue
            if "label_values" in it:
                plain, groups = _vals(it["label_values"])
                cname = fix_mojibake(str((plain.get("Name") or {}).get("value", ""))).strip() or None
                upd = _ts((plain.get("Update time") or {}).get("timestamp_value") or it.get("timestamp"), tz)
                for g in groups.values():
                    for m in g:
                        if isinstance(m, dict) and isinstance(m.get("dict"), list):
                            t = parse_labels(m["dict"], "saved")
                            if t:
                                add(t, coll=cname)
                                fallback.setdefault(t.key, upd)
            else:  # older shape: flat list mixing collection headers and members
                nm = ((it.get("string_map_data") or {}).get("Name") or {}).get("value")
                if it.get("title") == "Collection" and nm:
                    cname = fix_mojibake(str(nm)).strip()
                    continue
                old = _parse_old(it, "saved", tz)
                if old:
                    add(old[0], saved=old[1], coll=cname)

        for it in stream("liked_comments"):
            if isinstance(it, dict):
                old = _parse_old(it, "liked_comment", tz)
                if old:
                    old[0].key = "cm:" + old[0].key  # keep distinct from the post itself
                    add(old[0], liked=old[1])
        for it in stream("liked_threads"):
            if isinstance(it, dict):
                old = _parse_old(it, "liked_thread", tz)
                if old:
                    add(old[0], liked=old[1])
        for it in stream("saved_music"):
            if not isinstance(it, dict):
                continue
            plain, _ = _vals(it.get("label_values"))
            title = fix_mojibake(str((plain.get("Title") or {}).get("value", ""))).strip()
            artist = fix_mojibake(str((plain.get("Artist") or {}).get("value", ""))).strip()
            when = _ts((plain.get("Start time") or {}).get("timestamp_value") or it.get("timestamp"), tz)
            if title or artist:
                add(
                    Taste(kind="saved_music", key=f"m:{title}|{artist}",
                          caption=" - ".join(x for x in (title, artist) if x)),
                    saved=when,
                )
    for k, upd in fallback.items():
        r = recs[k]
        if r.saved_at is None and upd:
            r.saved_at = upd
            r.kind = "saved+liked" if r.liked_at else r.kind
    out = [r for r in recs.values() if r.when]
    if len(recs) - len(out):
        warnings.append(f"{len(recs) - len(out)} taste entries without a timestamp were skipped.")
    if not recs:
        warnings.append("No saved/liked data found in this zip (taste notes skipped).")
    return sorted(out, key=lambda r: (r.when, r.key)), warnings


def known_usernames(recs) -> frozenset:
    """Usernames seen anywhere in this export that are specific enough to scrub from other captions."""
    out = set()
    for r in recs:
        u = r.owner_user
        if len(u) >= 5 and (len(u) >= 7 or any(c in u for c in "_.0123456789")):
            out.add(u)
    return frozenset(out)


def clean_hashtags(t: Taste, do_redact: bool, known=frozenset()) -> list:
    # Hashtags are kept as-is even when redacting: they are public topic words (taste signal),
    # not personal data. Only @mentions and author names are hidden.
    return t.hashtags


def clean_caption(t: Taste, do_redact: bool, known=frozenset()) -> str:
    text = t.caption
    if not do_redact or not text:
        return text
    text = redact_contacts(text)
    text = _IG_PROFILE_IN_TEXT.sub("instagram.com/someone", text)
    text = MENTION_ANY.sub("@someone", text)
    for tok in sorted(t.owner_tokens, key=len, reverse=True):
        text = re.sub(re.escape(tok), "someone", text, flags=re.I)
    return scrub_known(text, known)


def _q(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def write_taste(recs: list[Taste], out: Path, do_redact=True, progress=None) -> tuple[int, int]:
    out = Path(out)
    existing = {p.stem.rsplit("_", 1)[-1] for p in out.rglob("*.md")} if out.exists() else set()
    written = skipped = 0
    known = known_usernames(recs) if do_redact else frozenset()
    for n, r in enumerate(recs):
        if progress and n % 2000 == 0:
            progress(n, len(recs))
        if r.sha8 in existing:
            skipped += 1
            continue
        w = r.when
        fm = [
            "---",
            f"source: {_q('threads' if r.kind == 'liked_thread' else 'instagram')}",
            f"kind: {_q(r.kind)}",
            f"collections: {_q(sorted(r.collections))}",
        ]
        if r.saved_at:
            fm.append(f"saved_at: {_q(r.saved_at.isoformat(timespec='seconds'))}")
        if r.liked_at:
            fm.append(f"liked_at: {_q(r.liked_at.isoformat(timespec='seconds'))}")
        fm += [
            f"owner_hash: {_q(r.owner_hash)}",
            f"url: {_q(r.url)}",
            f"hashtags: {_q(clean_hashtags(r, do_redact, known))}",
            f"sha8: {_q(r.sha8)}",
            "---",
        ]
        d = out / r.kind / str(w.year)
        d.mkdir(parents=True, exist_ok=True)
        text = clean_caption(r, do_redact, known)
        (d / f"{w:%Y-%m-%d}_{r.sha8}.md").write_text(
            "\n".join(fm) + "\n\n" + (text or "(no caption)") + "\n", encoding="utf-8"
        )
        existing.add(r.sha8)
        written += 1
    return written, skipped


def write_taste_bundle(recs: list[Taste], out: Path, do_redact=True, max_chars=400_000, max_files=45, info=None,
                       max_words=350_000, max_bytes=50 * 1024 * 1024) -> list[Path]:
    from .writers import pack_files

    known = known_usernames(recs) if do_redact else frozenset()
    items = []
    for r in recs:
        if not r.caption:
            continue
        col = f" | {', '.join(sorted(r.collections))}" if r.collections else ""
        ht = clean_hashtags(r, do_redact, known)
        tags = f"\n{' '.join('#' + h for h in ht)}" if ht else ""
        items.append(("taste", r.when.year,
                      f"## {r.when:%Y-%m-%d} | {r.kind}{col}\n{clean_caption(r, do_redact, known)}{tags}\n\n"))
    return pack_files(items, out, "Taste", "saved/liked posts by others, owners anonymized", max_chars, max_files,
                      max_words, max_bytes, info)


def taste_stats(recs: list[Taste]) -> dict:
    kinds, years, colls = defaultdict(int), defaultdict(int), set()
    for r in recs:
        kinds[r.kind] += 1
        years[r.when.year] += 1
        colls.update(r.collections)
    return {"total": len(recs), "kinds": dict(kinds), "collections": len(colls), "years": dict(sorted(years.items()))}
