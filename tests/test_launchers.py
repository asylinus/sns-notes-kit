import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class LauncherTest(unittest.TestCase):
    def test_bat_is_crlf(self):
        b = (ROOT / "run_windows.bat").read_bytes()
        lf = b.count(b"\n")
        crlf = b.count(b"\r\n")
        self.assertEqual(lf, crlf, "run_windows.bat must use CRLF or cmd.exe misreads it")


if __name__ == "__main__":
    unittest.main()
