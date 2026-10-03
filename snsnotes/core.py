"""Read a Meta (Instagram/Threads) JSON export zip directly, without extracting."""
import datetime as dt
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

POSTS_RE = re.compile(r"(^|/)(your_instagram_activity/media|content)/posts_\d+\.json$")
THREADS_RE = re.compile(r"(^|/)threads/threads_and_replies(_\d+)?\.json$")

_TZ_ALIASES = {"KST": 9, "UTC": 0, "GMT": 0, "JST": 9}


class ExportError(Exception):
    """User-facing error (message is shown as is)."""


@dataclass
class Entry:
    source: str  # "instagram" | "threads"
    when: dt.datetime  # timezone-aware
    kind: str  # "post" | "reply"
    text: str
    media: list[str] = field(default_factory=list)
    zip_name: str = ""

    @property
    def sha8(self) -> str:
        raw = f"{self.source}|{int(self.when.timestamp())}|{self.text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def fix_mojibake(s: str) -> str:
    """Meta writes UTF-8 bytes as latin1 escapes; undo that. Leave already-correct text alone."""
    try:
        return s.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def parse_tz(value: str | None) -> dt.tzinfo:
    if not value or value.lower() == "local":
        return dt.datetime.now().astimezone().tzinfo
    v = value.strip()
    if v.upper() in _TZ_ALIASES:
        return dt.timezone(dt.timedelta(hours=_TZ_ALIASES[v.upper()]))
    m = re.fullmatch(r"([+-])(\d{1,2})(?::?(\d{2}))?", v)
    if m:
        delta = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return dt.timezone(-delta if m.group(1) == "-" else delta)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(v)
    except Exception:
        raise ExportError(
            f"Unknown timezone '{value}'. Use e.g. KST, +09:00, or Asia/Seoul "
            "(on Windows the last one needs: pip install tzdata)."
        )


def _entries(items, source, tz, zip_name):
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        media = [m for m in (it.get("media") or []) if isinstance(m, dict)]
        ts = it.get("creation_timestamp") or next(
            (m.get("creation_timestamp") for m in media if m.get("creation_timestamp")), None
        )
        if not ts:
            continue
        raw = it.get("title") or next((m.get("title") for m in media if m.get("title")), "") or ""
        reply = bool(((media[0].get("text_app_post") or {}) if media else {}).get("is_reply"))
        yield Entry(
            source=source,
            when=dt.datetime.fromtimestamp(ts, tz),
            kind="reply" if reply else "post",
            text=fix_mojibake(str(raw)).strip(),
            media=[m["uri"] for m in media if m.get("uri")],
            zip_name=zip_name,
        )


def read_export(zip_path, tz: dt.tzinfo) -> tuple[list[Entry], list[str]]:
    """Return (entries sorted by time, warnings)."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise ExportError(f"File not found: {zip_path.name}")
    warnings: list[str] = []
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise ExportError(f"Not a valid zip file: {zip_path.name}")
    out: dict[tuple[str, str], Entry] = {}
    with zf:
        names = sorted(zf.namelist())
        post_files = [n for n in names if POSTS_RE.search(n)]
        thread_files = [n for n in names if THREADS_RE.search(n)]
        if not post_files:
            warnings.append("No Instagram posts file found (posts_N.json) - skipped.")
        if not thread_files:
            warnings.append("No Threads file found (threads_and_replies.json) - skipped.")
        if not post_files and not thread_files:
            raise ExportError(
                "This zip does not look like an Instagram/Threads JSON export. "
                "Did you choose JSON (not HTML) when requesting the download?"
            )
        for source, files in (("instagram", post_files), ("threads", thread_files)):
            for n in files:
                try:
                    data = json.loads(zf.read(n).decode("utf-8"))
                except (ValueError, KeyError) as e:
                    warnings.append(f"Could not read {n}: {e}")
                    continue
                items = data.get("text_post_app_text_posts", []) if isinstance(data, dict) else data
                for e in _entries(items, source, tz, zip_path.name):
                    out.setdefault((e.source, e.sha8), e)
    return sorted(out.values(), key=lambda e: (e.when, e.source)), warnings
