import tempfile
import unittest
from datetime import date
from pathlib import Path

from gallica_parser.global_parser import (
    GlobalConfig,
    GlobalGallicaParser,
    parse_sru_response,
    periodical_slug,
    sru_page_info,
)


SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/"
 xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
 <srw:numberOfRecords>1</srw:numberOfRecords><srw:records><srw:record><srw:recordData>
 <oai_dc:dc><dc:title>Journal de test</dc:title><dc:date>1815/09/30</dc:date>
 <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k12345</dc:identifier>
 <dc:relation>https://catalogue.bnf.fr/ark:/12148/cb12345678x</dc:relation></oai_dc:dc>
 </srw:recordData></srw:record></srw:records></srw:searchRetrieveResponse>'''


class GlobalParserTests(unittest.TestCase):
    def test_parses_sru_candidate(self):
        total, rows = parse_sru_response(SAMPLE)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].issue_ark, "bpt6k12345")
        self.assertEqual(rows[0].periodical_ark, "cb12345678x")
        self.assertEqual(rows[0].publication_date.isoformat(), "1815-09-30")

    def test_uses_sru_record_count_for_pagination(self):
        returned, next_position = sru_page_info(SAMPLE)
        self.assertEqual(returned, 1)
        self.assertIsNone(next_position)

    def test_shortens_long_periodical_folder_with_stable_hash(self):
        first = "Affiches et annonces " + "très longues " * 20
        second = first + "édition différente"
        self.assertLessEqual(len(periodical_slug(first)), 80)
        self.assertEqual(periodical_slug(first), periodical_slug(first))
        self.assertNotEqual(periodical_slug(first), periodical_slug(second))

    def test_keeps_existing_short_folder_name(self):
        self.assertEqual(periodical_slug("Journal de Paris"), "Journal_de_Paris")

    def test_persists_main_processing_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = GlobalConfig(
                date(1812, 1, 1), date(1825, 12, 31), {"Alexandre": ()},
                root / "output", root / "state.sqlite3", 1, 10, 1, False, False, (),
            )
            worker = GlobalGallicaParser(config)
            worker._save_main_cursor(17377)
            row = worker.state.connection.execute(
                "SELECT position FROM parser_progress WHERE name='main_candidates'"
            ).fetchone()
            worker.close()
            self.assertEqual(row[0], 17377)


if __name__ == "__main__":
    unittest.main()
