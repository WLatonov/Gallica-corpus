import tempfile
import unittest
from datetime import date
from pathlib import Path

from gallica_parser.models import Issue, SearchHit
from gallica_parser.state import StateDatabase


class StateTests(unittest.TestCase):
    def test_persists_hits_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            database = StateDatabase(Path(directory) / "state.sqlite3")
            try:
                issue = Issue("bd6test", date(1814, 9, 15), "15 septembre 1814")
                hit = SearchHit(issue.ark, "Alexandre", 8, "l'Empereur Alexandre")
                database.record_query(issue, "Alexandre", [hit])
                self.assertTrue(database.is_completed(issue.ark, "Alexandre"))
                self.assertEqual(database.get_hits(issue.ark, "Alexandre"), [hit])
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
