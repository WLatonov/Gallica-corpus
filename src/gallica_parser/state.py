from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Issue, SearchHit


class StateDatabase:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS issue_queries (
                issue_ark TEXT NOT NULL,
                publication_date TEXT NOT NULL,
                query TEXT NOT NULL,
                status TEXT NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (issue_ark, query)
            );
            CREATE TABLE IF NOT EXISTS downloads (
                issue_ark TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                output_path TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (issue_ark, page_number)
            );
            CREATE TABLE IF NOT EXISTS search_hits (
                issue_ark TEXT NOT NULL,
                query TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                context TEXT NOT NULL,
                PRIMARY KEY (issue_ark, query, page_number, context)
            );
            """
        )

    def close(self) -> None:
        self.connection.close()

    def is_completed(self, issue_ark: str, query: str) -> bool:
        row = self.connection.execute(
            "SELECT status FROM issue_queries WHERE issue_ark=? AND query=?",
            (issue_ark, query),
        ).fetchone()
        return bool(row and row[0] == "complete")

    def get_hits(self, issue_ark: str, query: str) -> list[SearchHit]:
        rows = self.connection.execute(
            """SELECT page_number, context FROM search_hits
               WHERE issue_ark=? AND query=? ORDER BY page_number""",
            (issue_ark, query),
        ).fetchall()
        return [SearchHit(issue_ark, query, int(row[0]), str(row[1])) for row in rows]

    def record_query(self, issue: Issue, query: str, hits: list[SearchHit]) -> None:
        self.connection.execute(
            "DELETE FROM search_hits WHERE issue_ark=? AND query=?", (issue.ark, query)
        )
        self.connection.executemany(
            """INSERT INTO search_hits(issue_ark, query, page_number, context)
               VALUES (?, ?, ?, ?)""",
            [(issue.ark, query, hit.page_number, hit.context) for hit in hits],
        )
        self.connection.execute(
            """
            INSERT INTO issue_queries(issue_ark, publication_date, query, status, hit_count, error)
            VALUES (?, ?, ?, 'complete', ?, NULL)
            ON CONFLICT(issue_ark, query) DO UPDATE SET
                status='complete', hit_count=excluded.hit_count, error=NULL,
                updated_at=CURRENT_TIMESTAMP
            """,
            (issue.ark, issue.publication_date.isoformat(), query, len(hits)),
        )
        self.connection.commit()

    def record_error(self, issue: Issue, query: str, error: str) -> None:
        self.connection.execute(
            """
            INSERT INTO issue_queries(issue_ark, publication_date, query, status, error)
            VALUES (?, ?, ?, 'error', ?)
            ON CONFLICT(issue_ark, query) DO UPDATE SET
                status='error', error=excluded.error, updated_at=CURRENT_TIMESTAMP
            """,
            (issue.ark, issue.publication_date.isoformat(), query, error),
        )
        self.connection.commit()

    def record_download(self, issue_ark: str, page_number: int, output_path: Path) -> None:
        self.connection.execute(
            """
            INSERT INTO downloads(issue_ark, page_number, output_path)
            VALUES (?, ?, ?)
            ON CONFLICT(issue_ark, page_number) DO UPDATE SET
                output_path=excluded.output_path, updated_at=CURRENT_TIMESTAMP
            """,
            (issue_ark, page_number, str(output_path)),
        )
        self.connection.commit()

    def is_downloaded(self, issue_ark: str, page_number: int) -> bool:
        row = self.connection.execute(
            "SELECT output_path FROM downloads WHERE issue_ark=? AND page_number=?",
            (issue_ark, page_number),
        ).fetchone()
        return bool(row and Path(row[0]).exists())
