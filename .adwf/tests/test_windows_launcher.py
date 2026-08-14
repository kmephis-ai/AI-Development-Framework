from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class WindowsLauncherTests(unittest.TestCase):
    def test_start_adwf_has_no_unix_style_quote_escaping(self):
        text = (ROOT / "START_ADWF.bat").read_text(encoding="utf-8")
        self.assertNotIn('\\"', text)
        self.assertIn('start "" "http://127.0.0.1:8765/"', text)
        self.assertIn('start "ADWF Executive Portal" /min py -3', text)
        self.assertIn('start "ADWF Executive Portal" /min python', text)
        self.assertIn('wait_portal.py', text)
        self.assertIn('ADWF v1.6 Executive Portal', text)


if __name__ == "__main__":
    unittest.main()
