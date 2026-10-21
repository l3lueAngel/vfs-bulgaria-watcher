import unittest
from unittest.mock import patch
import checker

class CheckerTests(unittest.TestCase):
    def test_record_id_stable_and_category_sensitive(self):
        a = {"category":"Application with Biometric", "center":"Izmir", "date_opened":"Waitlist opened", "reg_time":"2026-10-21 10:00:00"}
        b = dict(a, category="Application with Biometric (Individual - Bireysel)")
        self.assertEqual(checker.record_id(a), checker.record_id(dict(a)))
        self.assertNotEqual(checker.record_id(a), checker.record_id(b))

    def test_random_date_is_not_a_vfs_slot_signal(self):
        # The public page contains a date but no explicit slot availability phrase.
        html = "<html><body>Visa Application Centre hours: 21/10/2026. Please login.</body></html>"
        class FakeLocator:
            def __init__(self, text=""): self.text = text
            def inner_text(self, timeout=None): return self.text
            def count(self): return 0
        class FakePage:
            def __init__(self): self.locator_obj = FakeLocator(html)
            def goto(self, *a, **k):
                class R: status = 200
                return R()
            def wait_for_timeout(self, n): pass
            def locator(self, selector): return FakeLocator(html if selector == "body" else "")
        class FakeBrowser:
            def new_page(self, **kwargs): return FakePage()
            def close(self): pass
        class FakeChromium:
            def launch(self, **kwargs): return FakeBrowser()
        class FakePlaywright:
            chromium = FakeChromium()
            def __enter__(self): return self
            def __exit__(self, *args): pass
        class FakeSync:
            def __enter__(self): return FakePlaywright()
            def __exit__(self, *args): pass
        with patch("playwright.sync_api.sync_playwright", return_value=FakeSync()):
            findings, error = checker.check_official_vfs()
        self.assertIsNone(error)
        self.assertEqual(findings, [])

    def test_category_exclusion_terms(self):
        self.assertIn("child", checker.EXCLUDED)
        self.assertIn("without biometric", checker.EXCLUDED)

if __name__ == "__main__":
    unittest.main()