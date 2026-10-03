"""Local step-by-step wizard (standard library only).

Binds to 127.0.0.1 only, makes no outgoing connections, and stops when the browser tab goes away.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .core import HTML_MSG, ExportError, is_html_export, parse_tz, read_export
from .privacy import scan_text
from .taste import has_taste_files, known_usernames, read_taste, taste_stats, write_taste, write_taste_bundle
from .writers import DEFAULT_MAX_CHARS, write_bundle, write_notes

URLS = {
    "meta": "https://accountscenter.instagram.com/info_and_permissions/",
    "notebooklm": "https://notebooklm.google.com/",
}
NAME_HINT = re.compile(r"instagram|facebook|meta", re.I)
STAGES = ("read_posts", "read_taste", "notes", "taste", "report")
HTML_PATH = Path(__file__).with_name("wizard_ui.html")
KEEPALIVE_SECONDS = 45


def peak_mb() -> int:
    """Peak memory of this process in MB (0 if unknown)."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (n, ctypes.c_size_t)
                    for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                              "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                              "PagefileUsage", "PeakPagefileUsage")
                ]

            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p
            ps = ctypes.windll.psapi
            ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wintypes.DWORD]
            pm = PMC()
            pm.cb = ctypes.sizeof(pm)
            ps.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(pm), pm.cb)
            return pm.PeakWorkingSetSize // 1048576
        import resource

        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return v // 1048576 if sys.platform == "darwin" else v // 1024
    except Exception:
        return 0


def default_state_path() -> Path:
    return Path.home() / ".snsnotes" / "state.json"


def inspect_zip(path) -> dict:
    """Cheap check (central directory only): is this a usable JSON Meta export?"""
    p = Path(path)
    info = {"path": str(p), "name": p.name, "ok": False, "kind": "missing", "error": ""}
    if not p.is_file():
        info["error"] = "File not found."
        return info
    try:
        st = p.stat()
        info.update(size=st.st_size, mtime=int(st.st_mtime))
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        info.update(kind="notzip", error="Not a valid zip file. The download may be incomplete.")
        return info
    except OSError as e:
        info.update(kind="error", error=str(e.strerror or e))
        return info
    if is_html_export(names):
        info.update(kind="html", error=HTML_MSG)
        return info
    lower = [n.lower() for n in names]
    if any("your_instagram_activity" in n or n.endswith("posts_1.json") for n in lower) or has_taste_files(p):
        info.update(ok=True, kind="json")
    else:
        info.update(kind="unknown", error="This zip does not look like an Instagram/Threads JSON export.")
    return info


class Wizard:
    def __init__(self, state_path=None, downloads=None, upload_dir=None, out_root=None, preselect="",
                 timeout=KEEPALIVE_SECONDS, opener=None, url_opener=None):
        self.state_path = Path(state_path) if state_path else default_state_path()
        self.downloads = Path(downloads) if downloads else Path.home() / "Downloads"
        self.upload_dir = Path(upload_dir) if upload_dir else self.state_path.parent / "uploads"
        self.out_root = Path(out_root) if out_root else Path.home() / "SNS_notes"
        self.preselect = preselect
        self.timeout = timeout
        self.open_folder = opener or _open_folder
        self.open_url = url_opener or (lambda u: webbrowser.open(u))
        self.token = secrets.token_hex(16)
        self.last_seen = time.monotonic()
        self.server = None
        self.job = {"running": False, "stage": -1, "pct": 0, "done": False, "error": "", "result": None}
        self.job_lock = threading.Lock()
        self.opened: list = []  # (kind, target) log, handy for tests

    # ---- state -------------------------------------------------------
    def load_state(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, patch: dict) -> dict:
        st = self.load_state()
        for k in ("step", "lang", "theme", "zip", "out", "agreed"):
            if k in patch:
                st[k] = patch[k]
        st["updated"] = int(time.time())
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)
        return st

    # ---- find / upload -----------------------------------------------
    def find_zips(self) -> list[dict]:
        out = []
        try:
            files = [p for p in self.downloads.iterdir() if p.is_file() and p.suffix.lower() == ".zip"]
        except OSError:
            return out
        for p in files:
            info = inspect_zip(p) if NAME_HINT.search(p.name) else None
            if info is None:
                try:
                    with zipfile.ZipFile(p) as zf:
                        hit = any("your_instagram_activity" in n for n in zf.namelist()[:5000])
                except (OSError, zipfile.BadZipFile):
                    hit = False
                if not hit:
                    continue
                info = inspect_zip(p)
            if info["kind"] in ("json", "html", "unknown"):
                out.append(info)
        out.sort(key=lambda i: i.get("mtime", 0), reverse=True)
        return out[:30]

    def save_upload(self, name: str, stream, length: int) -> dict:
        safe = re.sub(r"[^\w.\-() ]+", "_", Path(name or "upload.zip").name, flags=re.U) or "upload.zip"
        if not safe.lower().endswith(".zip"):
            safe += ".zip"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        dest = self.upload_dir / safe
        left = length
        with open(dest, "wb") as f:
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left > 0:
            dest.unlink(missing_ok=True)
            raise ExportError("Upload was interrupted. Please try again.")
        return inspect_zip(dest)

    # ---- run -----------------------------------------------------------
    def start_run(self, path: str, out: str = "", tz: str = "local", account: str = "", taste=True) -> dict:
        info = inspect_zip(path)
        if not info["ok"]:
            return {"started": False, "check": info}
        with self.job_lock:
            if self.job["running"]:
                return {"started": False, "error": "already running"}
            self.job = {"running": True, "stage": 0, "pct": 0, "done": False, "error": "", "result": None}
        dest = Path(out) if out else self.out_root / Path(path).stem
        threading.Thread(target=self._run, args=(path, dest, tz, account, taste), daemon=True).start()
        return {"started": True, "out": str(dest)}

    def _set(self, stage=None, pct=None):
        with self.job_lock:
            if stage is not None:
                self.job["stage"] = stage
            if pct is not None:
                self.job["pct"] = int(pct)

    def _run(self, path, dest, tz, account, taste):
        t0 = time.time()
        try:
            self._set(0, 2)
            tzinfo = parse_tz(tz)
            try:
                entries, warns = read_export(path, tzinfo)
            except ExportError as e:
                if taste and has_taste_files(path) and "does not look like" in str(e):
                    entries, warns = [], []
                else:
                    raise
            self._set(1, 6)
            recs = None
            if taste and has_taste_files(path):
                recs, w2 = read_taste(path, tzinfo)
                warns += w2
            known = known_usernames(recs) if recs else frozenset()
            self._set(2, 22)
            dest.mkdir(parents=True, exist_ok=True)
            nw, ns = write_notes(entries, dest / "notes", True, account, known)
            nlm = write_bundle(entries, dest / "nlm", True, DEFAULT_MAX_CHARS, account, known)
            self._set(3, 28)
            tw = 0
            if recs:
                total = max(len(recs), 1)
                tw, _ = write_taste(recs, dest / "taste", True,
                                    progress=lambda n, t: self._set(pct=28 + 60 * n / total))
                nlm += write_taste_bundle(recs, dest / "nlm", True)
            self._set(4, 92)
            from .cli import build_report

            report = build_report(entries, account, recs)
            (dest / "scan_report.md").write_text(report, encoding="utf-8")
            masked = {"phone": 0, "email": 0, "account": 0, "mention": 0}
            texts = [e.text for e in entries] + [r.caption for r in (recs or []) if r.caption]
            for t in texts:
                for k, n in scan_text(t, account).items():
                    masked[k] += n
            st = taste_stats(recs) if recs else {"total": 0, "kinds": {}, "collections": 0, "years": {}}
            result = {
                "out": str(dest), "notes": len(entries), "notes_written": nw, "notes_skipped": ns,
                "taste": st["total"], "taste_written": tw, "kinds": st["kinds"],
                "collections": st["collections"], "years": st["years"],
                "nlm_files": len(list((dest / "nlm").glob("*.md"))), "masked": masked,
                "warnings": warns, "seconds": round(time.time() - t0, 1), "peak_mb": peak_mb(),
            }
            with self.job_lock:
                self.job.update(running=False, done=True, pct=100, stage=4, result=result)
        except ExportError as e:
            self._fail(str(e))
        except Exception as e:  # keep the wizard alive; show a readable message
            self._fail(f"{type(e).__name__}: {e}")

    def _fail(self, msg):
        with self.job_lock:
            self.job.update(running=False, done=False, error=msg)

    def progress(self) -> dict:
        with self.job_lock:
            return json.loads(json.dumps(self.job))

    # ---- actions -------------------------------------------------------
    def folder_target(self, which: str):
        r = (self.job.get("result") or {}).get("out") or self.load_state().get("out")
        if not r:
            return None
        base = Path(r)
        return {"out": base, "nlm": base / "nlm", "taste": base / "taste"}.get(which)

    def do_open_folder(self, which: str) -> bool:
        p = self.folder_target(which)
        if not p or not p.exists():
            return False
        self.opened.append(("folder", str(p)))
        self.open_folder(str(p))
        return True

    def do_open_url(self, which: str) -> bool:
        u = URLS.get(which)
        if not u:
            return False
        self.opened.append(("url", u))
        self.open_url(u)
        return True

    # ---- lifecycle -------------------------------------------------------
    def ping(self):
        self.last_seen = time.monotonic()

    def watchdog(self, poll=0.5):
        while self.server is not None:
            time.sleep(poll)
            if time.monotonic() - self.last_seen > self.timeout:
                self.stop()
                return

    def stop(self):
        s, self.server = self.server, None
        if s is not None:
            threading.Thread(target=s.shutdown, daemon=True).start()


def _open_folder(path: str):
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def make_handler(w: Wizard):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "snsnotes"

        def log_message(self, *a):  # quiet
            pass

        def _host_ok(self):
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost")

        def _json(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 1_000_000:
                return {}
            try:
                d = json.loads(self.rfile.read(n).decode("utf-8"))
                return d if isinstance(d, dict) else {}
            except ValueError:
                return {}

        def do_GET(self):
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                html = HTML_PATH.read_text(encoding="utf-8").replace("__TOKEN__", w.token)
                b = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b)
                return
            if not self._auth():
                return
            if u.path == "/api/state":
                return self._json({"state": w.load_state(), "preselect": w.preselect, "version": __version__,
                                   "stages": list(STAGES)})
            if u.path == "/api/progress":
                return self._json(w.progress())
            if u.path == "/api/ping":
                w.ping()
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

        def _auth(self):
            if self.headers.get("X-Token") != w.token:
                self._json({"error": "forbidden"}, 403)
                return False
            return True

        def do_POST(self):
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            if not self._auth():
                return
            u = urlparse(self.path)
            w.ping()
            if u.path == "/api/upload":
                q = parse_qs(u.query)
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    info = w.save_upload((q.get("name") or ["upload.zip"])[0], self.rfile, n)
                except ExportError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                return self._json(info)
            d = self._body()
            if u.path == "/api/state":
                return self._json({"state": w.save_state(d)})
            if u.path == "/api/find":
                return self._json({"files": w.find_zips(), "dir": str(w.downloads)})
            if u.path == "/api/check":
                return self._json(inspect_zip(d.get("path", "")))
            if u.path == "/api/run":
                r = w.start_run(d.get("path", ""), d.get("out", ""), d.get("tz", "local"), d.get("account", ""),
                                d.get("taste", True))
                return self._json(r, 200 if r.get("started") else 400)
            if u.path == "/api/open-folder":
                return self._json({"ok": w.do_open_folder(d.get("which", "out"))})
            if u.path == "/api/open-url":
                return self._json({"ok": w.do_open_url(d.get("which", ""))})
            if u.path == "/api/quit":
                self._json({"ok": True})
                w.stop()
                return
            self._json({"error": "not found"}, 404)

    return H


def make_server(w: Wizard, port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(w))
    srv.daemon_threads = True
    w.server = srv
    return srv


def serve(port=0, open_browser=True, zip_path="", **kw) -> int:
    w = Wizard(preselect=str(Path(zip_path).resolve()) if zip_path else "", **kw)
    srv = make_server(w, port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"snsnotes wizard: {url}  (local only; closing the browser tab stops it)")
    w.ping()
    threading.Thread(target=w.watchdog, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    print("snsnotes wizard: stopped.")
    return 0
