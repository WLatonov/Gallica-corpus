from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
from gallica_parser.config import load_config
from gallica_parser.models import Issue
from gallica_parser.parser import GallicaParser


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
config_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PROJECT / "configs" / "single_periodical.example.json"
config = load_config(config_path)
worker = GallicaParser(config, resume=True)
worker.client.delay_seconds = 10.0
worker.client.max_retries = 6

try:
    rows = worker.state.connection.execute(
        """SELECT issue_ark, publication_date, query
           FROM issue_queries WHERE status='error'
           ORDER BY publication_date, issue_ark, query"""
    ).fetchall()
    print(f"Запросов для восстановления: {len(rows)}")
    for position, (issue_ark, publication_date, query) in enumerate(rows, 1):
        issue = Issue(issue_ark, date.fromisoformat(publication_date), publication_date)
        print(f"[{position}/{len(rows)}] {publication_date} {issue_ark} {query}")
        try:
            hits = worker.client.search_issue(issue_ark, query)
            worker.state.record_query(issue, query, hits)
            by_page = defaultdict(list)
            for hit in hits:
                if worker._context_matches(query, hit.context):
                    by_page[hit.page_number].append(hit)
            for page_number, page_hits in by_page.items():
                if not worker.state.is_downloaded(issue_ark, page_number):
                    worker._download_page(issue, page_number, page_hits)
        except Exception as exc:
            worker.state.record_error(issue, query, str(exc))
            logging.exception("Не удалось восстановить запрос")
finally:
    remaining = worker.state.connection.execute(
        "SELECT count(1) FROM issue_queries WHERE status='error'"
    ).fetchone()[0]
    worker.close()
    print(f"Оставшихся ошибок: {remaining}")
