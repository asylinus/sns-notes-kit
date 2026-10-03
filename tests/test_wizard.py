import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_export  # noqa: E402
from snsnotes.wizard import Wizard, make_server  # noqa: E402


class Client:
    def __init__(self, w, port):
        self.w, self.port = w, port

    def req(self, method, path, body=None, raw=None, token=True, host=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        if token:
            h["X-Token"] = self.w.token
        if host:
            h["Host"] = host
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        txt = r.read().decode("utf-8")
        c.close()
        try:
            return r.status, json.loads(txt)
        except ValueError:
            return r.status, txt

    def get(self, p, **k):
        return self.req("GET", p, **k)

    def post(self, p, body=None, **k):
        return self.req("POST", p, body if body is not None else {}, **k)


class WizBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.dl = self.tmp / "Downloads"
        self.dl.mkdir()
        self.w = Wizard(state_path=self.tmp / "home" / "state.json", downloads=self.dl,
                        out_root=self.tmp / "out", timeout=3600,
                        opener=lambda p: None, url_opener=lambda u: None)
        self.srv = make_server(self.w, 0)
        self.th = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.th.start()
        self.c = Client(self.w, self.srv.server_address[1])

    def tearDown(self):
        self.w.server = None
        self.srv.shutdown()
        self.srv.server_close()
        self._tmp.cleanup()

    def wait_done(self, secs=60):
        t0 = time.time()
        while time.time() - t0 < secs:
            _, p = self.c.get("/api/progress")
            if p["done"] or p["error"]:
                return p
            time.sleep(0.1)
        self.fail("job did not finish")


class TestApi(WizBase):
    def test_ui_served_offline_with_token(self):
        st, html = self.c.get("/", token=False)
        self.assertEqual(st, 200)
        self.assertIn(self.w.token, html)
        for bad in ("googleapis", "gstatic", "http://", "cdn."):
            self.assertNotIn(bad, html.replace("https://www.w3.org", ""))

    def test_auth_and_host_checks(self):
        self.assertEqual(self.c.get("/api/state", token=False)[0], 403)
        self.assertEqual(self.c.post("/api/find", token=False)[0], 403)
        self.assertEqual(self.c.get("/api/state", host="evil.example.com")[0], 403)
        self.assertEqual(self.c.get("/api/state")[0], 200)
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_find_candidates(self):
        fake_export.build(self.dl / "instagram-me-2026.zip", taste=True)
        fake_export.build_html(self.dl / "meta-html.zip")
        fake_export.build(self.dl / "renamed.zip")  # no name hint, but has your_instagram_activity
        (self.dl / "other.zip").write_bytes(b"not a zip")
        import zipfile
        with zipfile.ZipFile(self.dl / "photos.zip", "w") as z:
            z.writestr("a.jpg", "x")
        st, r = self.c.post("/api/find")
        names = {f["name"]: f for f in r["files"]}
        self.assertEqual(set(names), {"instagram-me-2026.zip", "meta-html.zip", "renamed.zip"})
        self.assertEqual(names["instagram-me-2026.zip"]["kind"], "json")
        self.assertEqual(names["meta-html.zip"]["kind"], "html")
        self.assertIn("size", names["renamed.zip"])
        self.assertIn("mtime", names["renamed.zip"])

    def test_upload_streams_and_validates(self):
        z = fake_export.build(self.tmp / "up.zip", taste=True)
        st, info = self.c.req("POST", "/api/upload?name=my%20export.zip", raw=z.read_bytes())
        self.assertEqual(st, 200)
        self.assertTrue(info["ok"])
        self.assertTrue(Path(info["path"]).is_file())
        self.assertEqual(Path(info["path"]).read_bytes(), z.read_bytes())
        # path traversal in the name is neutralised
        st, info2 = self.c.req("POST", "/api/upload?name=..%2F..%2Fevil.zip", raw=z.read_bytes())
        self.assertEqual(Path(info2["path"]).parent, self.w.upload_dir)
        # HTML export is refused with a clear message
        h = fake_export.build_html(self.tmp / "h.zip")
        st, bad = self.c.req("POST", "/api/upload?name=h.zip", raw=h.read_bytes())
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["kind"], "html")
        self.assertIn("JSON", bad["error"])
        # not a zip at all
        st, bad2 = self.c.req("POST", "/api/upload?name=x.zip", raw=b"hello")
        self.assertFalse(bad2["ok"])

    def test_big_upload_in_chunks(self):
        blob = b"\0" * (5 * 1024 * 1024)
        st, info = self.c.req("POST", "/api/upload?name=big.zip", raw=blob)
        self.assertEqual(st, 200)
        self.assertEqual(Path(info["path"]).stat().st_size, len(blob))

    def test_run_progress_and_summary(self):
        z = fake_export.build(self.tmp / "e.zip", taste=True)
        st, r = self.c.post("/api/run", {"path": str(z), "out": str(self.tmp / "res")})
        self.assertEqual(st, 200)
        self.assertTrue(r["started"])
        p = self.wait_done()
        self.assertEqual(p["error"], "")
        res = p["result"]
        self.assertEqual(res["notes"], 7)
        self.assertEqual(res["taste"], 6)
        self.assertEqual(res["collections"], 1)
        self.assertEqual(res["masked"]["phone"], 2)  # own post + taste caption
        self.assertGreaterEqual(res["masked"]["email"], 2)
        self.assertEqual(p["pct"], 100)
        out = Path(res["out"])
        self.assertEqual(len(list((out / "notes").rglob("*.md"))), 7)
        self.assertEqual(len(list((out / "taste").rglob("*.md"))), 6)
        self.assertTrue((out / "scan_report.md").is_file())
        self.assertEqual(res["nlm_files"], len(list((out / "nlm").glob("*.md"))))
        txt = "\n".join(p.read_text(encoding="utf-8") for p in out.rglob("*.md")).lower()
        self.assertNotIn("stranger_one", txt)
        self.assertNotIn("010-9999-8888", txt)
        # second run is idempotent
        self.c.post("/api/run", {"path": str(z), "out": str(out)})
        p2 = self.wait_done()
        self.assertEqual(p2["result"]["notes_written"], 0)
        self.assertEqual(p2["result"]["taste_written"], 0)

    def test_run_rejects_html_and_missing(self):
        h = fake_export.build_html(self.tmp / "h.zip")
        st, r = self.c.post("/api/run", {"path": str(h)})
        self.assertEqual(st, 400)
        self.assertFalse(r["started"])
        self.assertIn("JSON", r["check"]["error"])
        st, r = self.c.post("/api/run", {"path": str(self.tmp / "nope.zip")})
        self.assertEqual(st, 400)

    def test_state_save_and_restore(self):
        self.c.post("/api/state", {"step": 3, "lang": "en", "theme": "dark", "zip": "C:/x.zip", "junk": 1})
        w2 = Wizard(state_path=self.w.state_path)
        st = w2.load_state()
        self.assertEqual((st["step"], st["lang"], st["theme"], st["zip"]), (3, "en", "dark", "C:/x.zip"))
        self.assertNotIn("junk", st)
        _, r = self.c.get("/api/state")
        self.assertEqual(r["state"]["step"], 3)
        self.w.state_path.write_text("{broken", encoding="utf-8")  # corrupt file -> start fresh
        self.assertEqual(self.c.get("/api/state")[1]["state"], {})

    def test_open_actions_whitelisted(self):
        opened = []
        self.w.open_folder = lambda p: opened.append(("f", p))
        self.w.open_url = lambda u: opened.append(("u", u))
        z = fake_export.build(self.tmp / "e.zip")
        self.c.post("/api/run", {"path": str(z), "out": str(self.tmp / "res")})
        self.wait_done()
        self.assertTrue(self.c.post("/api/open-folder", {"which": "nlm"})[1]["ok"])
        self.assertTrue(self.c.post("/api/open-folder", {"which": "out"})[1]["ok"])
        self.assertFalse(self.c.post("/api/open-folder", {"which": "../../etc"})[1]["ok"])
        self.assertTrue(self.c.post("/api/open-url", {"which": "notebooklm"})[1]["ok"])
        self.assertTrue(self.c.post("/api/open-url", {"which": "meta"})[1]["ok"])
        self.assertFalse(self.c.post("/api/open-url", {"which": "http://evil.example"})[1]["ok"])
        urls = [x[1] for x in opened if x[0] == "u"]
        self.assertTrue(urls[0].startswith("https://notebooklm.google.com"))
        self.assertTrue(urls[1].startswith("https://accountscenter.instagram.com"))
        self.assertTrue(opened[0][1].endswith("nlm"))

    def test_quit_endpoint_stops_server(self):
        self.assertEqual(self.c.post("/api/quit")[0], 200)
        self.th.join(5)
        self.assertFalse(self.th.is_alive())


class TestKeepalive(unittest.TestCase):
    def serve(self, timeout):
        self._tmp = tempfile.TemporaryDirectory()
        w = Wizard(state_path=Path(self._tmp.name) / "s.json", downloads=self._tmp.name, timeout=timeout)
        srv = make_server(w, 0)
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        threading.Thread(target=w.watchdog, kwargs={"poll": 0.1}, daemon=True).start()
        return w, srv, th

    def tearDown(self):
        self._tmp.cleanup()

    def test_stops_when_pings_stop(self):
        w, srv, th = self.serve(timeout=1.0)
        t0 = time.time()
        th.join(6)
        self.assertFalse(th.is_alive())
        self.assertLess(time.time() - t0, 5)
        srv.server_close()

    def test_pings_keep_it_alive(self):
        w, srv, th = self.serve(timeout=1.0)
        c = Client(w, srv.server_address[1])
        for _ in range(12):  # ~2.4 s > timeout, but pinged every 0.2 s
            c.get("/api/ping")
            time.sleep(0.2)
        self.assertTrue(th.is_alive())
        th.join(6)  # now stop pinging
        self.assertFalse(th.is_alive())
        srv.server_close()


if __name__ == "__main__":
    unittest.main()
