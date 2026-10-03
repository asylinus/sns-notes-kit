import unittest, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
class LauncherTest(unittest.TestCase):
    def test_bat_is_crlf(self):
        b = (ROOT / "run_windows.bat").read_bytes()
        self.assertEqual(b.count(b"
"), b.count(b"
"), "run_windows.bat must use CRLF or cmd.exe misreads it")
