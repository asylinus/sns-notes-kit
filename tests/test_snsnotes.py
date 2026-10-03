import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_export  # noqa: E402
from snsnotes import cli  # noqa: E402
from snsnotes.core import ExportError, fix_mojibake, parse_tz, read_export  # noqa: E402
from snsnotes.privacy import redact, scan_text  # noqa: E402
from snsnotes.writers import write_bundle, write_notes  # noqa: E402

KST = parse_tz("+09:00")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.zip = fake_export.build(self.tmp / "fake.zip")

    def tearDown(self):
        self._tmp.cleanup()


class TestRead(Base):
    def test_mojibake_fix(self):
        self.assertEqual(fix_mojibake(fake_export.meta_str("안녕")), "안녕")
        self.assertEqual(fix_mojibake("already fine 안녕"), "already fine 안녕")
        entries, _ = read_export(self.zip, KST)
        self.assertIn("안녕하세요 첫 번째 게시물입니다", [e.text for e in entries])

    def test_multiple_posts_files_and_threads(self):
        entries, warns = read_export(self.zip, KST)
        self.assertEqual(len(entries), 7)  # 3 + 2 (posts_1, posts_2) + 2 threads
        self.assertEqual(sum(e.source == "threads" for e in entries), 2)
        self.assertEqual(sum(e.kind == "reply" for e in entries), 1)
        self.assertEqual(warns, [])

    def test_timezone(self):
        e_kst = read_export(self.zip, KST)[0][0]
        e_utc = read_export(self.zip, parse_tz("UTC"))[0][0]
        self.assertEqual(e_kst.when.hour, 10)
        self.assertEqual(e_utc.when.hour, 1)

    def test_missing_threads_is_warning(self):
        z = fake_export.build(self.tmp / "p.zip", only_posts=True)
        entries, warns = read_export(z, KST)
        self.assertEqual(len(entries), 5)
        self.assertTrue(any("Threads" in w for w in warns))

    def test_unrelated_zip_errors(self):
        z = self.tmp / "x.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("hello.txt", "hi")
        with self.assertRaises(ExportError):
            read_export(z, KST)

    def test_bad_timezone(self):
        with self.assertRaises(ExportError):
            parse_tz("Nowhere/Land")


class TestNotes(Base):
    def test_notes_count_layout_and_idempotent(self):
        entries, _ = read_export(self.zip, KST)
        out = self.tmp / "out"
        self.assertEqual(write_notes(entries, out), (7, 0))
        files = list(out.rglob("*.md"))
        self.assertEqual(len(files), 7)
        self.assertTrue((out / "instagram" / "2023").is_dir())
        self.assertTrue((out / "threads" / "2024").is_dir())
        first = sorted((out / "instagram" / "2023").glob("*.md"))[0]
        self.assertRegex(first.name, r"^2023-05-01_1000_[0-9a-f]{8}\.md$")
        body = first.read_text(encoding="utf-8")
        self.assertTrue(body.startswith("---\nsource: \"instagram\""))
        self.assertIn("sha8:", body)
        self.assertIn("안녕하세요 첫 번째 게시물입니다", body)
        self.assertEqual(write_notes(entries, out), (0, 7))  # second run skips all
        self.assertEqual(len(list(out.rglob("*.md"))), 7)


class TestBundle(Base):
    def test_per_source_year(self):
        entries, _ = read_export(self.zip, KST)
        files = write_bundle(entries, self.tmp / "nlm")
        self.assertEqual(
            sorted(p.name for p in files),
            ["instagram_2023.md", "instagram_2024.md", "threads_2024.md"],
        )
        self.assertTrue(files[0].read_text(encoding="utf-8").startswith("# Instagram 2023"))

    def test_split_under_limit(self):
        z = fake_export.build(self.tmp / "big.zip", big_posts=40)
        entries, _ = read_export(z, KST)
        limit = 3000
        files = write_bundle(entries, self.tmp / "nlm", max_chars=limit)
        parts = [p for p in files if "2022" in p.name]
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p.read_text(encoding="utf-8")), limit)
        self.assertIn("part 1/", parts[0].read_text(encoding="utf-8").splitlines()[0])


class TestPrivacy(Base):
    def test_scan_detection(self):
        entries, _ = read_export(self.zip, KST)
        tot = {"phone": 0, "email": 0, "account": 0, "mention": 0}
        for e in entries:
            for k, n in scan_text(e.text, "my_own_handle").items():
                tot[k] += n
        self.assertEqual(tot, {"phone": 1, "email": 1, "account": 1, "mention": 1})

    def test_dates_not_flagged(self):
        c = scan_text("2024-01-02 and 2023-12-31 meeting at 10:30")
        self.assertEqual(sum(c.values()), 0)

    def test_redact(self):
        t = redact("tel 010-1234-5678 mail a.b@example.com acct 110-123-456789 ok")
        self.assertNotIn("1234", t)
        self.assertNotIn("example.com", t)
        self.assertNotIn("456789", t)
        self.assertEqual(t.count("[REDACTED-"), 3)

    def test_redact_in_outputs(self):
        entries, _ = read_export(self.zip, KST)
        write_notes(entries, self.tmp / "n", do_redact=True)
        write_bundle(entries, self.tmp / "b", do_redact=True)
        for p in list((self.tmp / "n").rglob("*.md")) + list((self.tmp / "b").glob("*.md")):
            s = p.read_text(encoding="utf-8")
            self.assertNotIn("010-1234-5678", s)
            self.assertNotIn("example.com", s)
            self.assertNotIn("110-123-456789", s)

    def test_report_has_no_values(self):
        entries, _ = read_export(self.zip, KST)
        rep = cli.build_report(entries)
        self.assertIn("Phone numbers: 1", rep)
        for secret in ("010-1234", "example.com", "110-123", "fake_friend"):
            self.assertNotIn(secret, rep)


class TestCli(Base):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*args])
        return code, out.getvalue(), err.getvalue()

    def test_all_command(self):
        code, out, _ = self.run_cli("all", str(self.zip), "--tz", "+09:00")
        self.assertEqual(code, 0)
        base = self.tmp / "fake_snsnotes"
        self.assertEqual(len(list((base / "notes").rglob("*.md"))), 7)
        self.assertTrue((base / "scan_report.md").is_file())
        self.assertEqual(len(list((base / "nlm").glob("*.md"))), 3)

    def test_missing_file_message(self):
        code, _, err = self.run_cli("scan", str(self.tmp / "nope.zip"))
        self.assertEqual(code, 2)
        self.assertIn("not found", err.lower())


if __name__ == "__main__":
    unittest.main()
