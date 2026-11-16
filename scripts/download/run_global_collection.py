from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
from gallica_parser.global_parser import GlobalGallicaParser, load_global_config


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
LOGGER = logging.getLogger("global-runner")
CONFIG = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PROJECT / "configs" / "alexander_holy_alliance_1812_1825.json"
COOLDOWN_SECONDS = 15 * 60


def counts(worker: GlobalGallicaParser) -> tuple[int, int, int, int]:
    db = worker.state.connection
    candidates = db.execute("select count(1) from candidates").fetchone()[0]
    discovery_errors = db.execute(
        "select count(1) from discovery_pages where status='error'"
    ).fetchone()[0]
    query_errors = db.execute(
        "select count(1) from issue_queries where status='error'"
    ).fetchone()[0]
    downloads = db.execute("select count(1) from downloads").fetchone()[0]
    return candidates, discovery_errors, query_errors, downloads


while True:
    worker = GlobalGallicaParser(load_global_config(CONFIG))
    try:
        LOGGER.info("Начало/возобновление глобального обнаружения")
        worker.discover()
        LOGGER.info("Обнаружение завершено; начало обработки кандидатов")
        worker.process_candidates()
        current = counts(worker)
        LOGGER.info(
            "Состояние: кандидаты=%d, ошибки SRU=%d, ошибки OCR=%d, страницы=%d",
            *current,
        )
        if current[1] == 0 and current[2] == 0:
            LOGGER.info("GLOBAL_COLLECTION_COMPLETE %s", datetime.now().isoformat())
            break
    except Exception:
        LOGGER.exception("Проход прерван внешней ошибкой Gallica")
    finally:
        worker.close()
    LOGGER.info("Следующая попытка через %d секунд", COOLDOWN_SECONDS)
    time.sleep(COOLDOWN_SECONDS)
