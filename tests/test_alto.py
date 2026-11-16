import unittest

from gallica_parser.alto import extract_alto_text


ALTO = b"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"><Layout><Page><PrintSpace><TextBlock>
<TextLine><String CONTENT="l'"/><String CONTENT="Em" SUBS_TYPE="HypPart1" SUBS_CONTENT="Empereur"/></TextLine>
<TextLine><String CONTENT="pereur" SUBS_TYPE="HypPart2" SUBS_CONTENT="Empereur"/><String CONTENT="Alexandre"/><String CONTENT="."/></TextLine>
</TextBlock></PrintSpace></Page></Layout></alto>"""


class AltoTests(unittest.TestCase):
    def test_extracts_and_joins_hyphenated_words(self):
        raw, clean = extract_alto_text(ALTO)
        self.assertIn("Em\npereur", raw)
        self.assertEqual(clean, "l'Empereur Alexandre.")


if __name__ == "__main__":
    unittest.main()

