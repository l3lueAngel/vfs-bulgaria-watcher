import re
import html
import json
import hashlib
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Import our checker module
import checker

SAMPLE_INDIVIDUAL_HTML = """
<div class="card-surface">
  <span title="Application with Biometric ( Individual - Bireysel)">Application with Biometric ( Individual - Bireysel)</span>
  <span>Earliest Date Opened:</span> <strong>Bekleme Listesi Acildi</strong>
  <span>Center:</span> <strong>Istanbul Altunizade</strong>
  <span>2026-10-11 17:02:14</span>
</div>
<div class="card-surface">
  <span title="Application with Biometric ( Individual - Bireysel)">Application with Biometric ( Individual - Bireysel)</span>
  <span>Earliest Date Opened:</span> <strong>20 Ekim 2026</strong>
  <span>Center:</span> <strong>Edirne</strong>
  <span>2026-10-09 11:28:37</span>
</div>
<div class="card-surface">
  <span title="Application without Biometric ( Individual - Bireysel)">Application without Biometric ( Individual - Bireysel)</span>
  <span>Earliest Date Opened:</span> <strong>15 Kasim 2026</strong>
  <span>Center:</span> <strong>Ankara</strong>
  <span>2026-10-12 10:00:00</span>
</div>
<div class="card-surface">
  <span title="Application with Biometric (0-12)">Application with Biometric (0-12)</span>
  <span>Earliest Date Opened:</span> <strong>16 Kasim 2026</strong>
  <span>Center:</span> <strong>Izmir</strong>
  <span>2026-10-12 11:00:00</span>
</div>
"""

SAMPLE_BIOMETRIC_HTML = """
<div class="card-surface">
  <span title="Application with Biometric">Application with Biometric</span>
  <span>Earliest Date Opened:</span> <strong>Bekleme Listesi Acildi</strong>
  <span>Center:</span> <strong>Izmir</strong>
  <span>2026-10-13 15:00:34</span>
</div>
<div class="card-surface">
  <span title="Application with Biometric">Application with Biometric</span>
  <span>Earliest Date Opened:</span> <strong>Bekleme Listesi Acildi</strong>
  <span>Center:</span> <strong>Ankara</strong>
  <span>2026-10-13 07:04:13</span>
</div>
<div class="card-surface">
  <span title="Application with Biometric - Child">Application with Biometric - Child</span>
  <span>Earliest Date Opened:</span> <strong>Bekleme Listesi Acildi</strong>
  <span>Center:</span> <strong>Bursa</strong>
  <span>2026-10-13 06:00:00</span>
</div>
"""

class TestWatcherV31(unittest.TestCase):

    def test_parser_filters_and_categories(self):
        # 1. Test individual parsing & exclusion of without biometric / 0-12
        cards_ind = checker.parse_visatour_cards(SAMPLE_INDIVIDUAL_HTML)
        self.assertEqual(len(cards_ind), 2, "Without biometric ve 0-12 elenmeli, sadece 2 bireysel kalmali")
        self.assertEqual(cards_ind[0]["center"], "Edirne")
        self.assertEqual(cards_ind[1]["center"], "Istanbul Altunizade")

        # 2. Test general biometric parsing & exclusion of child
        cards_bio = checker.parse_visatour_cards(SAMPLE_BIOMETRIC_HTML)
        self.assertEqual(len(cards_bio), 2, "Child kategorisi elenmeli, sadece 2 biyometrik kalmali")
        self.assertEqual(cards_bio[0]["center"], "Ankara")
        self.assertEqual(cards_bio[1]["center"], "Izmir")

    def test_record_id_uniqueness(self):
        card1 = {
            "category": "Application with Biometric ( Individual - Bireysel)",
            "center": "Ankara",
            "date_opened": "Bekleme Listesi Acildi",
            "reg_time": "2026-10-13 07:04:13"
        }
        card2 = {
            "category": "Application with Biometric",
            "center": "Ankara",
            "date_opened": "Bekleme Listesi Acildi",
            "reg_time": "2026-10-13 07:04:13"
        }
        id1 = checker.make_record_id(card1)
        id2 = checker.make_record_id(card2)
        self.assertNotEqual(id1, id2, "Farkli kategorilerin SHA-256 hash'leri farkli olmali")

    def test_slot_message_titles_and_loudness(self):
        card_ind = {
            "category": "Application with Biometric ( Individual - Bireysel)",
            "center": "Istanbul Altunizade",
            "date_opened": "Bekleme Listesi Acildi",
            "reg_time": "2026-10-13 18:00:00"
        }
        card_bio = {
            "category": "Application with Biometric",
            "center": "Izmir",
            "date_opened": "Bekleme Listesi Acildi",
            "reg_time": "2026-10-13 18:00:00"
        }

        msg_ind = checker.slot_message(card_ind)
        self.assertIn("BİREYSEL BİYOMETRİK RANDEVU HAREKETİ", msg_ind)
        self.assertIn("Application with Biometric ( Individual - Bireysel)", msg_ind)

        msg_bio = checker.slot_message(card_bio)
        self.assertIn("BİYOMETRİK RANDEVU HAREKETİ", msg_bio)
        self.assertNotIn("BİREYSEL", msg_bio)
        self.assertIn("Application with Biometric", msg_bio)

    def test_outage_error_tracking_flow(self):
        # Test error tracking logic
        mock_outage_state = {"consecutive_errors": 0, "outage_notified": False}
        telegram_calls = []

        def mock_send(msg, silent=False):
            telegram_calls.append({"msg": msg, "silent": silent})
            return True

        with patch("checker.load_outage_state", return_value=mock_outage_state), \
             patch("checker.save_outage_state", side_effect=lambda s: mock_outage_state.update(s)), \
             patch("checker.send_telegram", side_effect=mock_send):

            dummy_time = datetime(2026, 10, 13, 14, 30, tzinfo=checker.TZ_TR) # minute 30 -> no hourly report

            # 1-4 errors -> NO telegram messages
            for i in range(1, 5):
                checker.handle_error(RuntimeError("HTTP 530"), dummy_time)
                self.assertEqual(len(telegram_calls), 0, f"Error {i} must not send telegram message")
                self.assertEqual(mock_outage_state["consecutive_errors"], i)
                self.assertFalse(mock_outage_state["outage_notified"])

            # 5th consecutive error -> 1 SILENT telegram outage alert
            checker.handle_error(RuntimeError("HTTP 530"), dummy_time)
            self.assertEqual(len(telegram_calls), 1, "5th error must send exactly 1 telegram message")
            self.assertTrue(telegram_calls[-1]["silent"], "Outage alert must be silent")
            self.assertIn("yaklaşık 10 dakikadır erişilemiyor", telegram_calls[-1]["msg"])
            self.assertTrue(mock_outage_state["outage_notified"])

            # 6th error -> NO additional telegram message
            checker.handle_error(RuntimeError("HTTP 530"), dummy_time)
            self.assertEqual(len(telegram_calls), 1, "6th error must not send another message")
            self.assertEqual(mock_outage_state["consecutive_errors"], 6)

            # Recovery -> 1 SILENT telegram recovery alert, reset counter
            checker.handle_success(dummy_time, [])
            self.assertEqual(len(telegram_calls), 2, "Recovery must send exactly 1 telegram message")
            self.assertTrue(telegram_calls[-1]["silent"], "Recovery must be silent")
            self.assertIn("tekrar erişilebilir", telegram_calls[-1]["msg"])
            self.assertEqual(mock_outage_state["consecutive_errors"], 0)
            self.assertFalse(mock_outage_state["outage_notified"])

    def test_hourly_report_on_error_vs_success(self):
        telegram_calls = []
        def mock_send(msg, silent=False):
            telegram_calls.append({"msg": msg, "silent": silent})
            return True

        hourly_time = datetime(2026, 10, 13, 15, 0, tzinfo=checker.TZ_TR) # minute 0 -> hourly report

        with patch("checker.load_outage_state", return_value={"consecutive_errors": 0, "outage_notified": False}), \
             patch("checker.save_outage_state"), \
             patch("checker.send_telegram", side_effect=mock_send):

            # When error happens at minute 0:
            checker.handle_error(RuntimeError("HTTP 530"), hourly_time)
            self.assertEqual(len(telegram_calls), 1)
            self.assertTrue(telegram_calls[0]["silent"])
            self.assertIn("Son kontrol VisaTour erişim hatası nedeniyle tamamlanamadı", telegram_calls[0]["msg"])

if __name__ == "__main__":
    unittest.main()
