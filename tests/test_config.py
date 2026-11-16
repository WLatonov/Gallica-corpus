import json
import tempfile
import unittest
from pathlib import Path

from gallica_parser.config import load_config


class ConfigTests(unittest.TestCase):
    def test_normalizes_periodical_ark_and_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "periodical_ark": "ark:/12148/cb327966882/date",
                        "periodical_title": "Journal de la Lozère",
                        "date_from": "1814-09-01",
                        "date_to": "1815-07-01",
                        "queries": ["Alexandre"],
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.periodical_ark, "cb327966882")
            self.assertEqual(config.output_directory, Path(directory) / "data")


if __name__ == "__main__":
    unittest.main()
