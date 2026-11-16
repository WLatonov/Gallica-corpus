from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from gallica_parser.global_parser import GlobalGallicaParser, load_global_config  # noqa: E402


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
LOGGER = logging.getLogger("event-collection")
CONFIG_PATH = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PROJECT / "configs" / "events_original_download_1812_1822.json"
COOLDOWN_SECONDS = 15 * 60


class EventGallicaParser(GlobalGallicaParser):
    # Clear markers of scholarly, professional and literary periodicals.  The
    # bare word "journal" is deliberately absent: Journal de Paris, Journal du
    # Cher and many other historical newspapers use it in their proper names.
    NON_NEWSPAPER_MARKERS = (
        "médecine", "medecine", "chirurgie", "pharmacie", "médical", "medical",
        "éducation", "education", "instruction publique",
        "agronom", "horticulture", "vétérinaire", "veterinaire", "physique",
        "chimie", "sciences médicales", "sciences medicales",
        "académie", "academie", "société savante", "societe savante",
        "annales", "bulletin", "mémoires", "memoires", "revue",
        "bibliothèque", "bibliotheque", "magasin encyclopédique",
        "magasin encyclopedique", "mercure de france", "journal des savants",
        "journal d'éducation", "journal d’éducation", "journal de santé",
        "journal de sante", "journal des connaissances", "journal d'agriculture",
        "journal d’agriculture", "journal de pharmacie", "journal de médecine",
        "journal de medecine", "journal de chirurgie", "journal militaire des",
    )

    def __init__(self, config, date_ranges: dict[str, tuple[int, int]]) -> None:
        super().__init__(config)
        self.date_ranges = date_ranges

    def discover(self) -> int:
        for query in self.config.searches:
            start_year, end_year = self.date_ranges[query]
            for year in range(start_year, end_year + 1):
                self._discover_query_year(year, query)
        return self.state.connection.execute("SELECT count(1) FROM candidates").fetchone()[0]

    def candidate_skip_reason(self, candidate) -> str | None:
        title = candidate.title.casefold()
        marker = next((x for x in self.NON_NEWSPAPER_MARKERS if x in title), None)
        return f"не газета: маркер «{marker}»" if marker else None


def counts(worker: GlobalGallicaParser) -> tuple[int, int, int, int]:
    db = worker.state.connection
    candidates = db.execute("SELECT count(1) FROM candidates").fetchone()[0]
    discovery_errors = db.execute(
        "SELECT count(1) FROM discovery_pages WHERE status='error'"
    ).fetchone()[0]
    query_errors = db.execute(
        "SELECT count(1) FROM issue_queries WHERE status='error'"
    ).fetchone()[0]
    downloads = db.execute("SELECT count(1) FROM downloads").fetchone()[0]
    return candidates, discovery_errors, query_errors, downloads


raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
date_ranges = {
    query: (int(years[0]), int(years[1]))
    for query, years in raw["search_date_ranges"].items()
}
missing = set(raw["searches"]) - set(date_ranges)
if missing:
    raise ValueError(f"Нет диапазонов дат для запросов: {sorted(missing)}")

while True:
    worker = EventGallicaParser(load_global_config(CONFIG_PATH), date_ranges)
    try:
        LOGGER.info("Начало/возобновление поиска выпусков по историческим событиям")
        worker.discover()
        LOGGER.info("Поиск выпусков завершён; начало обработки кандидатов")
        worker.process_candidates()
        current = counts(worker)
        LOGGER.info(
            "Состояние: кандидаты=%d, ошибки SRU=%d, ошибки OCR=%d, страницы=%d",
            *current,
        )
        if current[1] == 0 and current[2] == 0:
            LOGGER.info("EVENT_COLLECTION_COMPLETE %s", datetime.now().isoformat())
            break
    except Exception:
        LOGGER.exception("Проход прерван внешней ошибкой Gallica")
    finally:
        worker.close()
    LOGGER.info("Следующая попытка через %d секунд", COOLDOWN_SECONDS)
    time.sleep(COOLDOWN_SECONDS)
