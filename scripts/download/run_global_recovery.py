from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
from gallica_parser.global_parser import Candidate, GlobalGallicaParser, load_global_config


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
LOGGER = logging.getLogger("global-recovery")
CONFIG = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PROJECT / "configs" / "alexander_holy_alliance_1812_1825.json"
COOLDOWN_SECONDS = 15 * 60
MAX_REQUEST_RETRIES = 3


def ensure_recovery_schema(worker: GlobalGallicaParser) -> None:
    worker.state.connection.execute(
        """CREATE TABLE IF NOT EXISTS page_recovery (
               issue_ark TEXT NOT NULL,
               page_number INTEGER NOT NULL,
               attempts INTEGER NOT NULL DEFAULT 0,
               next_retry_at TEXT,
               last_error TEXT,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
               PRIMARY KEY(issue_ark,page_number)
           )"""
    )
    worker.state.connection.commit()


def retry_delay_hours(attempts: int) -> int:
    if attempts <= 1:
        return 6
    if attempts == 2:
        return 12
    return 24


def utc_sql_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def retry_queries(worker: GlobalGallicaParser) -> tuple[int, int]:
    db = worker.state.connection
    rows = db.execute(
        """SELECT c.issue_ark, c.title, c.publication_date, c.periodical_ark,
                  j.value AS query
           FROM candidates c, json_each(c.discovery_queries) j
           LEFT JOIN issue_queries q
             ON q.issue_ark=c.issue_ark AND q.query=j.value
           WHERE q.issue_ark IS NULL OR q.status='error'
           ORDER BY c.publication_date,c.issue_ark,j.value"""
    ).fetchall()
    recovered = 0
    for index, (ark, title, publication_date, periodical_ark, query) in enumerate(rows, 1):
        LOGGER.info("Запрос [%d/%d] %s %s %s", index, len(rows), publication_date, ark, query)
        try:
            found = worker.client.search_issue(ark, query)
            hits = [(hit.page_number, hit.context) for hit in found]
            worker._record_issue_query(ark, query, hits)
            recovered += 1
        except Exception as exc:
            worker._record_issue_error(ark, query, str(exc))
            LOGGER.exception("Не удалось восстановить запрос %s/%s", ark, query)
    return len(rows), recovered


def relevant_pages(worker: GlobalGallicaParser) -> list[tuple[Candidate, int, list[dict[str, object]]]]:
    db = worker.state.connection
    rows = db.execute(
        """SELECT c.issue_ark,c.title,c.publication_date,c.periodical_ark,
                  h.query,h.page_number,h.context
           FROM search_hits h JOIN candidates c USING(issue_ark)
           ORDER BY c.publication_date,c.issue_ark,h.page_number"""
    ).fetchall()
    pages: dict[tuple[str, int], tuple[Candidate, list[dict[str, object]]]] = {}
    for ark, title, publication_date, periodical_ark, query, page, context in rows:
        required = worker.config.searches.get(query, ())
        if required and not any(term in context.casefold() for term in required):
            continue
        key = (ark, int(page))
        if key not in pages:
            pages[key] = (
                Candidate(ark, title, date.fromisoformat(publication_date), periodical_ark),
                [],
            )
        pages[key][1].append({"query": query, "context": context})
    return [(candidate, page, hits) for (_, page), (candidate, hits) in pages.items()]


def recover_pages(worker: GlobalGallicaParser) -> tuple[int, int, int]:
    db = worker.state.connection
    missing: list[tuple[Candidate, int, list[dict[str, object]]]] = []
    deferred = 0
    repaired_paths = 0
    for candidate, page, hits in relevant_pages(worker):
        row = db.execute(
            "SELECT output_path FROM downloads WHERE issue_ark=? AND page_number=?",
            (candidate.issue_ark, page),
        ).fetchone()
        if row and Path(row[0]).exists():
            continue
        expected = (
            worker._periodical_directory(candidate.title)
            / candidate.publication_date.isoformat()
            / candidate.issue_ark
        )
        metadata = expected / f"page_{page}_metadata.json"
        if row and metadata.exists():
            db.execute(
                "UPDATE downloads SET output_path=?,updated_at=CURRENT_TIMESTAMP WHERE issue_ark=? AND page_number=?",
                (str(expected), candidate.issue_ark, page),
            )
            repaired_paths += 1
            continue
        recovery = db.execute(
            "SELECT next_retry_at FROM page_recovery WHERE issue_ark=? AND page_number=?",
            (candidate.issue_ark, page),
        ).fetchone()
        if recovery and recovery[0] and recovery[0] > utc_sql_timestamp(datetime.now(timezone.utc)):
            deferred += 1
            continue
        missing.append((candidate, page, hits))
    db.commit()
    outstanding = len(missing) + deferred
    LOGGER.info(
        "Недостающих страниц=%d; доступны сейчас=%d; отложены=%d; исправлено путей=%d",
        outstanding, len(missing), deferred, repaired_paths,
    )
    recovered = 0
    for index, (candidate, page, hits) in enumerate(missing, 1):
        LOGGER.info(
            "Страница [%d/%d] %s %s/f%d", index, len(missing),
            candidate.publication_date, candidate.issue_ark, page,
        )
        try:
            worker._download(candidate, page, hits)
            db.execute(
                "DELETE FROM page_recovery WHERE issue_ark=? AND page_number=?",
                (candidate.issue_ark, page),
            )
            db.commit()
            recovered += 1
        except Exception as exc:
            previous = db.execute(
                "SELECT attempts FROM page_recovery WHERE issue_ark=? AND page_number=?",
                (candidate.issue_ark, page),
            ).fetchone()
            attempts = int(previous[0]) + 1 if previous else 1
            retry_at = datetime.now(timezone.utc) + timedelta(hours=retry_delay_hours(attempts))
            db.execute(
                """INSERT INTO page_recovery(issue_ark,page_number,attempts,next_retry_at,last_error)
                   VALUES(?,?,?,?,?) ON CONFLICT(issue_ark,page_number) DO UPDATE SET
                   attempts=excluded.attempts,next_retry_at=excluded.next_retry_at,
                   last_error=excluded.last_error,updated_at=CURRENT_TIMESTAMP""",
                (candidate.issue_ark, page, attempts, utc_sql_timestamp(retry_at), str(exc)),
            )
            db.commit()
            LOGGER.exception("Не удалось восстановить страницу %s/f%d", candidate.issue_ark, page)
    return outstanding, len(missing), recovered


def main() -> None:
    while True:
        worker = GlobalGallicaParser(load_global_config(CONFIG))
        worker.client.max_retries = min(worker.client.max_retries, MAX_REQUEST_RETRIES)
        ensure_recovery_schema(worker)
        try:
            query_total, query_recovered = retry_queries(worker)
            outstanding, eligible, page_recovered = recover_pages(worker)
            remaining_queries = worker.state.connection.execute(
                "SELECT count(*) FROM issue_queries WHERE status='error'"
            ).fetchone()[0]
            LOGGER.info(
                "RECOVERY_STATE запросы=%d/%d страницы=%d/%d доступны=%d осталось_ошибок=%d",
                query_recovered, query_total, page_recovered, outstanding,
                eligible, remaining_queries,
            )
            if remaining_queries == 0 and outstanding == page_recovered:
                LOGGER.info("GLOBAL_RECOVERY_COMPLETE")
                break
        except Exception:
            LOGGER.exception("Проход восстановления прерван")
        finally:
            worker.close()
        LOGGER.info("Следующий адаптивный проход через %d секунд", COOLDOWN_SECONDS)
        time.sleep(COOLDOWN_SECONDS)


if __name__ == "__main__":
    main()
