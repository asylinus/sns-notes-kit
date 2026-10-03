import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_export  # noqa: E402
from test_snsnotes import KST, Base  # noqa: E402
from snsnotes import cli  # noqa: E402
from snsnotes.stream import iter_array  # noqa: E402
from snsnotes.taste import owner_hash, read_taste, write_taste, write_taste_bundle  # noqa: E402


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main([*args])
    return code, out.getvalue(), err.getvalue()


def all_text(d):
    return "\n".join(p.read_text(encoding="utf-8") for p in Path(d).rglob("*.md"))


class TestTaste(Base):
    def setUp(self):
        super().setUp()
        self.zip = fake_export.build(self.tmp / "t.zip", taste=True)

    def test_counts_and_merge(self):
        recs, _ = read_taste(self.zip, KST)
        self.assertEqual(len(recs), 6)  # AAA saved+liked merged, BBB, CCC, thread, comment, music
        self.assertEqual(
            sorted(r.kind for r in recs),
            sorted(["saved+liked", "saved", "liked", "liked_thread", "liked_comment", "saved_music"]),
        )
        a = [r for r in recs if r.kind == "saved+liked"][0]
        self.assertEqual(a.collections, {"여행 모음"})
        self.assertEqual(a.hashtags, ["여행", "맛집"])
        out = self.tmp / "taste"
        self.assertEqual(write_taste(recs, out), (6, 0))
        self.assertEqual(len(list(out.rglob("*.md"))), 6)  # merged post = one note
        self.assertEqual(write_taste(recs, out), (0, 6))
        note = next((out / "saved+liked" / "2021").glob("2021-03-01_*.md")).read_text(encoding="utf-8")
        for k in ("source:", 'kind: "saved+liked"', 'collections: ["여행 모음"]', "saved_at:", "liked_at:",
                  "owner_hash:", "url:", "hashtags:", "sha8:"):
            self.assertIn(k, note)
        self.assertIn(owner_hash("stranger_one"), note)

    def test_owner_names_never_written(self):
        recs, _ = read_taste(self.zip, KST)
        for red in (True, False):
            out = self.tmp / f"o{red}"
            write_taste(recs, out, do_redact=red)
            write_taste_bundle(recs, out / "nlm", do_redact=red)
            txt = all_text(out).lower()
            if red:  # caption mentions of the owner are scrubbed too
                for name in ("stranger_one", "stranger_two", "stranger one", "stranger two"):
                    self.assertNotIn(name, txt)
            self.assertNotIn("instagram.com/stranger", txt)
            self.assertNotIn("@stranger_one/", txt)
            self.assertNotIn("owner:", txt)
        # fields: only the hash
        self.assertNotIn("stranger", all_text(self.tmp / "oTrue").lower())

    def test_redact_default_and_no_redact(self):
        for flags, masked in (([], True), (["--no-redact"], False)):
            out = self.tmp / f"c{int(masked)}"
            code, _, _ = run("taste", str(self.zip), "-o", str(out), *flags)
            self.assertEqual(code, 0)
            txt = all_text(out)
            self.assertEqual("010-9999-8888" not in txt, masked)
            self.assertEqual("me@example.org" not in txt, masked)
            self.assertEqual("@someone_else" not in txt, masked)
            self.assertEqual("@someone " in txt, masked)
            self.assertNotIn("Stranger One", txt)  # display name never stored in any mode

    def test_taste_is_experimental_opt_in(self):
        self.assertEqual(run("all", str(self.zip), "-o", str(self.tmp / "A"), "--tz", "+09:00", "--taste", "--notes")[0], 0)
        self.assertEqual(run("all", str(self.zip), "-o", str(self.tmp / "B"))[0], 0)
        A, B = self.tmp / "A", self.tmp / "B"
        self.assertEqual(len(list((A / "taste").rglob("*.md"))), 6)
        self.assertTrue((A / "nlm" / "2_내취향" / "taste_2021.md").is_file())
        self.assertFalse((B / "taste").exists())
        self.assertFalse((B / "nlm" / "2_내취향").exists())
        rep = (A / "scan_report.md").read_text(encoding="utf-8")
        self.assertIn("Taste (saved / liked): 6 entries, 1 collections", rep)
        self.assertIn("2021: 1", rep)
        self.assertNotIn("stranger", rep.lower())
        self.assertNotIn("Taste", (B / "scan_report.md").read_text(encoding="utf-8"))

    def test_notes_bundle_redact_default(self):
        out = self.tmp / "R"
        run("notes", str(self.zip), "-o", str(out))
        self.assertNotIn("010-1234-5678", all_text(out))
        out2 = self.tmp / "R2"
        run("bundle", str(self.zip), "-o", str(out2), "--no-redact")
        self.assertIn("010-1234-5678", all_text(out2))
        out3 = self.tmp / "R3"
        run("bundle", str(self.zip), "-o", str(out3))
        self.assertNotIn("010-1234-5678", all_text(out3))

    def test_taste_only_zip(self):
        z = fake_export.build_taste_only(self.tmp / "only.zip")
        self.assertEqual(run("all", str(z), "-o", str(self.tmp / "T"), "--taste", "--notes")[0], 0)
        self.assertEqual(len(list((self.tmp / "T" / "taste").rglob("*.md"))), 6)

    def test_html_export_friendly_error(self):
        z = fake_export.build_html(self.tmp / "h.zip")
        for cmd in ("all", "taste"):
            code, _, err = run(cmd, str(z), "-o", str(self.tmp / "H"))
            self.assertEqual(code, 2)
            self.assertIn("JSON", err)
            self.assertIn("HTML", err)


class TestScrub(Base):
    def test_mentions_masked_in_own_notes_by_default(self):
        out = self.tmp / "m"
        run("notes", str(self.zip), "-o", str(out), "--account", "my_own_handle")
        txt = all_text(out)
        self.assertNotIn("fake_friend", txt)
        self.assertIn("@my_own_handle", txt)
        out2 = self.tmp / "m2"
        run("notes", str(self.zip), "-o", str(out2), "--no-redact")
        self.assertIn("fake_friend", all_text(out2))

    def test_known_usernames_scrubbed_across_records(self):
        from snsnotes.taste import Taste, clean_caption, clean_hashtags, known_usernames
        a = Taste(kind="saved", key="a", caption="x", owner_user="loft_interior")
        b = Taste(kind="saved", key="b", caption="see loft_interior shop and cafe", hashtags=["Loft_Interior", "cafe"])
        known = known_usernames([a, b])
        self.assertNotIn("loft_interior", clean_caption(b, True, known).lower())
        self.assertIn("cafe", clean_caption(b, True, known))
        self.assertEqual(clean_hashtags(b, True, known), ["Loft_Interior", "cafe"])  # hashtags kept
        c = Taste(kind="saved", key="c", caption="loved it #loft_interior by loft_interior", hashtags=[])
        out = clean_caption(c, True, known)
        self.assertIn("#loft_interior", out)          # hashtag in caption kept
        self.assertNotIn(" by loft_interior", out)    # bare handle still hidden
        self.assertIn("loft_interior", clean_caption(b, False, known))


class TestStream(unittest.TestCase):
    def test_iter_array_chunks(self):
        data = json.dumps({"k": [{"a": "한" * 50, "i": i} for i in range(200)]}, ensure_ascii=False).encode("utf-8")
        got = list(iter_array(io.BytesIO(data), chunk=7))
        self.assertEqual([g["i"] for g in got], list(range(200)))
        self.assertEqual(list(iter_array(io.BytesIO(b"[]"))), [])


if __name__ == "__main__":
    unittest.main()
