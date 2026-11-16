import unittest
from datetime import datetime, timezone

from scripts.download.run_global_recovery import retry_delay_hours, utc_sql_timestamp


class RecoveryTests(unittest.TestCase):
    def test_adaptive_retry_delays(self):
        self.assertEqual(retry_delay_hours(1), 6)
        self.assertEqual(retry_delay_hours(2), 12)
        self.assertEqual(retry_delay_hours(3), 24)
        self.assertEqual(retry_delay_hours(20), 24)

    def test_utc_timestamp_is_sql_sortable(self):
        value = datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(utc_sql_timestamp(value), "2026-08-19 12:30:00")


if __name__ == "__main__":
    unittest.main()
