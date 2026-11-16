from __future__ import annotations

import csv
import hashlib
import html
import json
import logging
import re
import sqlite3
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .alto import extract_alto_text
from .client import BASE_URL, GallicaClient

LOGGER = logging.getLogger(__name__)
SRU_NS = "http://www.loc.gov/zing/srw/"
DC_NS = "http://purl.org/dc/elements/1.1/"


@dataclass(frozen=True, slots=True)
class GlobalConfig:
    date_from: date
    date_to: date
    searches: dict[str, tuple[str, ...]]
    output_directory: Path
    database_path: Path
    request_delay_seconds: float
    request_timeout_seconds: float
    max_retries: int
    download_images: bool
    download_alto: bool
    exclude_periodical_arks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    issue_ark: str
    title: str
    publication_date: date
    periodical_ark: str | None


def load_global_config(path: str | Path) -> GlobalConfig:
    config_path = Path(path).resolve()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent
    start = date.fromisoformat(data["date_from"])
    end = date.fromisoformat(data["date_to"])
    if start > end:
        raise ValueError("date_from не может быть позже date_to")
    searches = {
        str(query): tuple(str(term).casefold() for term in terms)
        for query, terms in data["searches"].items()
    }
    if not searches:
        raise ValueError("searches не может быть пустым")
    return GlobalConfig(
        date_from=start,
        date_to=end,
        searches=searches,
        output_directory=(base / data["output_directory"]).resolve(),
        database_path=(base / data["database_path"]).resolve(),
        request_delay_seconds=max(1.0, float(data.get("request_delay_seconds", 10))),
        request_timeout_seconds=max(10.0, float(data.get("request_timeout_seconds", 60))),
        max_retries=max(1, int(data.get("max_retries", 8))),
        download_images=bool(data.get("download_images", True)),
        download_alto=bool(data.get("download_alto", True)),
        exclude_periodical_arks=tuple(data.get("exclude_periodical_arks", [])),
    )


class GlobalState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS discovery_pages (
                year INTEGER NOT NULL, query TEXT NOT NULL, start_record INTEGER NOT NULL,
                status TEXT NOT NULL, result_count INTEGER, error TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(year, query, start_record)
            );
            CREATE TABLE IF NOT EXISTS candidates (
                issue_ark TEXT PRIMARY KEY, title TEXT NOT NULL, publication_date TEXT NOT NULL,
                periodical_ark TEXT, discovery_queries TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS issue_queries (
                issue_ark TEXT NOT NULL, query TEXT NOT NULL, status TEXT NOT NULL,
                hit_count INTEGER DEFAULT 0, error TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(issue_ark, query)
            );
            CREATE TABLE IF NOT EXISTS search_hits (
                issue_ark TEXT NOT NULL, query TEXT NOT NULL, page_number INTEGER NOT NULL,
                context TEXT NOT NULL,
                PRIMARY KEY(issue_ark, query, page_number, context)
            );
            CREATE TABLE IF NOT EXISTS downloads (
                issue_ark TEXT NOT NULL, page_number INTEGER NOT NULL, output_path TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(issue_ark, page_number)
            );
            CREATE TABLE IF NOT EXISTS parser_progress (
                name TEXT PRIMARY KEY, position INTEGER NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS skipped_candidates (
                issue_ark TEXT PRIMARY KEY, title TEXT NOT NULL, reason TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class GlobalGallicaParser:
    def __init__(self, config: GlobalConfig) -> None:
        self.config = config
        self.state = GlobalState(config.database_path)
        self.client = GallicaClient(
            delay_seconds=config.request_delay_seconds,
            timeout_seconds=config.request_timeout_seconds,
            max_retries=config.max_retries,
        )

    def close(self) -> None:
        self.state.close()

    def candidate_skip_reason(self, candidate: Candidate) -> str | None:
        """Subclass hook for collection-specific source-type filtering."""
        return None

    def discover(self) -> int:
        for year in range(self.config.date_from.year, self.config.date_to.year + 1):
            for query in self.config.searches:
                self._discover_query_year(year, query)
        return self.state.connection.execute("SELECT count(1) FROM candidates").fetchone()[0]

    def _discover_query_year(self, year: int, query: str) -> None:
        start_record = 1
        while True:
            done = self.state.connection.execute(
                "SELECT status,result_count FROM discovery_pages WHERE year=? AND query=? AND start_record=?",
                (year, query, start_record),
            ).fetchone()
            if done and done[0] == "complete":
                count = int(done[1] or 0)
                if count < 50:
                    return
                start_record += 50
                continue
            LOGGER.info("SRU: %d, %s, запись %d", year, query, start_record)
            try:
                xml_data = self._sru_search(year, query, start_record)
                total, candidates = parse_sru_response(xml_data)
                returned_records, next_position = sru_page_info(xml_data)
                self._save_candidates(candidates, query)
                self.state.connection.execute(
                    """INSERT INTO discovery_pages(year,query,start_record,status,result_count,error)
                       VALUES(?,?,?,'complete',?,NULL)
                       ON CONFLICT(year,query,start_record) DO UPDATE SET
                       status='complete',result_count=excluded.result_count,error=NULL,
                       updated_at=CURRENT_TIMESTAMP""",
                    (year, query, start_record, returned_records),
                )
                self.state.connection.commit()
            except Exception as exc:
                self.state.connection.execute(
                    """INSERT INTO discovery_pages(year,query,start_record,status,error)
                       VALUES(?,?,?,'error',?) ON CONFLICT(year,query,start_record) DO UPDATE SET
                       status='error',error=excluded.error,updated_at=CURRENT_TIMESTAMP""",
                    (year, query, start_record, str(exc)),
                )
                self.state.connection.commit()
                raise
            if returned_records == 0 or next_position is None or next_position > total:
                return
            start_record = next_position

    def _sru_search(self, year: int, query: str, start_record: int) -> bytes:
        cql = (
            f'(gallica all "{query}") and (dc.type all "fascicule") and '
            f'(gallicapublication_date>="{year}/01/01" and '
            f'gallicapublication_date<="{year}/12/31")'
        )
        params = urllib.parse.urlencode(
            {
                "operation": "searchRetrieve", "version": "1.2", "query": cql,
                "collapsing": "false", "maximumRecords": 50,
                "startRecord": start_record, "suggest": 0,
            }
        )
        return self.client.get(f"{BASE_URL}/SRU?{params}")

    def _save_candidates(self, candidates: list[Candidate], query: str) -> None:
        excluded = set(self.config.exclude_periodical_arks)
        for item in candidates:
            if item.periodical_ark in excluded:
                continue
            row = self.state.connection.execute(
                "SELECT discovery_queries FROM candidates WHERE issue_ark=?", (item.issue_ark,)
            ).fetchone()
            queries = set(json.loads(row[0])) if row else set()
            queries.add(query)
            self.state.connection.execute(
                """INSERT INTO candidates(issue_ark,title,publication_date,periodical_ark,discovery_queries)
                   VALUES(?,?,?,?,?) ON CONFLICT(issue_ark) DO UPDATE SET
                   title=excluded.title,publication_date=excluded.publication_date,
                   periodical_ark=COALESCE(excluded.periodical_ark,candidates.periodical_ark),
                   discovery_queries=excluded.discovery_queries""",
                (item.issue_ark, item.title, item.publication_date.isoformat(),
                 item.periodical_ark, json.dumps(sorted(queries), ensure_ascii=False)),
            )

    def process_candidates(self, *, limit: int | None = None) -> dict[str, int]:
        rows = self.state.connection.execute(
            "SELECT issue_ark,title,publication_date,periodical_ark,discovery_queries FROM candidates ORDER BY publication_date,issue_ark"
        ).fetchall()
        if limit is not None:
            rows = rows[:limit]
        cursor_row = self.state.connection.execute(
            "SELECT position FROM parser_progress WHERE name='main_candidates'"
        ).fetchone()
        cursor = int(cursor_row[0]) if cursor_row else 0
        if cursor:
            LOGGER.info("Возобновление основного сбора после позиции %d", cursor)
        saved = 0
        for index, row in enumerate(rows, 1):
            if index <= cursor:
                continue
            candidate = Candidate(row[0], row[1], date.fromisoformat(row[2]), row[3])
            skip_reason = self.candidate_skip_reason(candidate)
            if skip_reason:
                LOGGER.info("[%d/%d] ПРОПУСК: %s — %s (%s)", index, len(rows),
                            candidate.publication_date, candidate.title, skip_reason)
                self.state.connection.execute(
                    """INSERT INTO skipped_candidates(issue_ark,title,reason) VALUES(?,?,?)
                       ON CONFLICT(issue_ark) DO UPDATE SET title=excluded.title,
                       reason=excluded.reason,updated_at=CURRENT_TIMESTAMP""",
                    (candidate.issue_ark, candidate.title, skip_reason),
                )
                self.state.connection.commit()
                self._save_main_cursor(index)
                continue
            discovery_queries = json.loads(row[4])
            LOGGER.info("[%d/%d] %s — %s", index, len(rows), candidate.publication_date, candidate.title)
            page_hits: dict[int, list[dict[str, object]]] = {}
            for query in discovery_queries:
                status = self.state.connection.execute(
                    "SELECT status FROM issue_queries WHERE issue_ark=? AND query=?",
                    (candidate.issue_ark, query),
                ).fetchone()
                if status and status[0] == "complete":
                    hits = self.state.connection.execute(
                        "SELECT page_number,context FROM search_hits WHERE issue_ark=? AND query=?",
                        (candidate.issue_ark, query),
                    ).fetchall()
                else:
                    try:
                        found = self.client.search_issue(candidate.issue_ark, query)
                        hits = [(hit.page_number, hit.context) for hit in found]
                        self._record_issue_query(candidate.issue_ark, query, hits)
                    except Exception as exc:
                        self._record_issue_error(candidate.issue_ark, query, str(exc))
                        continue
                required = self.config.searches[query]
                for page_number, context in hits:
                    if required and not any(term in context.casefold() for term in required):
                        continue
                    page_hits.setdefault(int(page_number), []).append(
                        {"query": query, "context": context}
                    )
            for page_number, hits in page_hits.items():
                exists = self.state.connection.execute(
                    "SELECT output_path FROM downloads WHERE issue_ark=? AND page_number=?",
                    (candidate.issue_ark, page_number),
                ).fetchone()
                if exists and Path(exists[0]).exists():
                    continue
                try:
                    self._download(candidate, page_number, hits)
                    saved += 1
                except Exception:
                    LOGGER.exception("Ошибка загрузки %s/f%d", candidate.issue_ark, page_number)
            self._save_main_cursor(index)
        return {"candidates": len(rows), "saved": saved}

    def _save_main_cursor(self, position: int) -> None:
        self.state.connection.execute(
            """INSERT INTO parser_progress(name,position) VALUES('main_candidates',?)
               ON CONFLICT(name) DO UPDATE SET position=excluded.position,
               updated_at=CURRENT_TIMESTAMP""",
            (position,),
        )
        self.state.connection.commit()

    def _record_issue_query(self, issue_ark: str, query: str, hits: list[tuple[int, str]]) -> None:
        self.state.connection.execute(
            "DELETE FROM search_hits WHERE issue_ark=? AND query=?", (issue_ark, query)
        )
        self.state.connection.executemany(
            "INSERT INTO search_hits(issue_ark,query,page_number,context) VALUES(?,?,?,?)",
            [(issue_ark, query, page, context) for page, context in hits],
        )
        self.state.connection.execute(
            """INSERT INTO issue_queries(issue_ark,query,status,hit_count,error)
               VALUES(?,?,'complete',?,NULL) ON CONFLICT(issue_ark,query) DO UPDATE SET
               status='complete',hit_count=excluded.hit_count,error=NULL,updated_at=CURRENT_TIMESTAMP""",
            (issue_ark, query, len(hits)),
        )
        self.state.connection.commit()

    def _record_issue_error(self, issue_ark: str, query: str, error: str) -> None:
        self.state.connection.execute(
            """INSERT INTO issue_queries(issue_ark,query,status,error) VALUES(?,?,'error',?)
               ON CONFLICT(issue_ark,query) DO UPDATE SET status='error',error=excluded.error,
               updated_at=CURRENT_TIMESTAMP""",
            (issue_ark, query, error),
        )
        self.state.connection.commit()

    def _download(self, candidate: Candidate, page: int, hits: list[dict[str, object]]) -> None:
        periodical_directory = self._periodical_directory(candidate.title)
        target = periodical_directory / candidate.publication_date.isoformat() / candidate.issue_ark
        target.mkdir(parents=True, exist_ok=True)
        alto = self.client.get_alto(candidate.issue_ark, page)
        raw, clean = extract_alto_text(alto)
        files: dict[str, str] = {}
        if self.config.download_alto:
            path = target / f"page_{page}_alto.xml"; path.write_bytes(alto)
            files[path.name] = sha256(alto)
        path = target / f"page_{page}_ocr.txt"; path.write_text(raw, encoding="utf-8")
        files[path.name] = sha256(raw.encode())
        path = target / f"page_{page}_ocr_clean.txt"; path.write_text(clean, encoding="utf-8")
        files[path.name] = sha256(clean.encode())
        if self.config.download_images:
            try:
                image = self.client.get_page_image(candidate.issue_ark, page)
                path = target / f"page_{page}.jpg"; path.write_bytes(image)
                files[path.name] = sha256(image)
            except Exception as exc:
                files["page_image_error"] = str(exc)
        metadata = {
            "periodical_title": candidate.title,
            "periodical_ark": candidate.periodical_ark,
            "issue_ark": f"ark:/12148/{candidate.issue_ark}",
            "issue_date": candidate.publication_date.isoformat(), "digital_page": page,
            "issue_url": f"{BASE_URL}/ark:/12148/{candidate.issue_ark}",
            "page_url": f"{BASE_URL}/ark:/12148/{candidate.issue_ark}/f{page}.item",
            "hits": hits, "files_sha256": files,
        }
        (target / f"page_{page}_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.state.connection.execute(
            "INSERT OR REPLACE INTO downloads(issue_ark,page_number,output_path) VALUES(?,?,?)",
            (candidate.issue_ark, page, str(target)),
        )
        self.state.connection.commit()

    def _periodical_directory(self, title: str) -> Path:
        """Return a Windows-safe folder and migrate the former long folder if needed."""
        legacy = self.config.output_directory / safe_slug(title)
        compact = self.config.output_directory / periodical_slug(title)
        if compact != legacy and legacy.exists() and not compact.exists():
            LOGGER.info("Переименование слишком длинного каталога: %s -> %s", legacy.name, compact.name)
            legacy.rename(compact)
        return compact


def parse_sru_response(xml_data: bytes) -> tuple[int, list[Candidate]]:
    root = ET.fromstring(xml_data)
    total_node = root.find(f".//{{{SRU_NS}}}numberOfRecords")
    total = int(total_node.text or 0) if total_node is not None else 0
    candidates: list[Candidate] = []
    for record in root.findall(f".//{{{SRU_NS}}}recordData"):
        fields: dict[str, list[str]] = {}
        for node in record.iter():
            if node.tag.startswith(f"{{{DC_NS}}}") and node.text:
                fields.setdefault(node.tag.rsplit("}", 1)[-1], []).append(node.text.strip())
        identifiers = fields.get("identifier", []) + fields.get("source", [])
        issue_ark = _find_ark(identifiers, prefixes=("bpt", "btv", "bd"))
        if not issue_ark:
            continue
        periodical_ark = _find_ark(fields.get("relation", []) + identifiers, prefixes=("cb",))
        publication_date = _find_date(fields.get("date", []) + fields.get("description", []))
        if not publication_date:
            continue
        title = next((value for value in fields.get("title", []) if value), "Périodique sans titre")
        candidates.append(Candidate(issue_ark, title, publication_date, periodical_ark))
    return total, candidates


def sru_page_info(xml_data: bytes) -> tuple[int, int | None]:
    """Возвращает число записей SRU на странице и позицию следующей страницы."""
    root = ET.fromstring(xml_data)
    returned = len(root.findall(f".//{{{SRU_NS}}}record"))
    next_node = root.find(f".//{{{SRU_NS}}}nextRecordPosition")
    next_position = (
        int(next_node.text) if next_node is not None and next_node.text else None
    )
    return returned, next_position


def _find_ark(values: list[str], prefixes: tuple[str, ...]) -> str | None:
    for value in values:
        for match in re.findall(r"(?:ark:/12148/)?([a-z]+[a-z0-9]+)", value.casefold()):
            if match.startswith(prefixes):
                return match
    return None


def _find_date(values: list[str]) -> date | None:
    for value in values:
        match = re.search(r"(18\d{2})[-/](\d{2})[-/](\d{2})", value)
        if match:
            try:
                return date(*(int(part) for part in match.groups()))
            except ValueError:
                pass
    return None


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^\w.-]+", "_", html.unescape(value), flags=re.UNICODE).strip("_.")
    return slug[:120] or "periodique_sans_titre"


def periodical_slug(value: str, max_length: int = 80) -> str:
    """Build a short, deterministic and collision-resistant periodical folder name."""
    legacy = safe_slug(value)
    if len(legacy) <= max_length:
        return legacy
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    prefix_length = max_length - len(digest) - 2
    return f"{legacy[:prefix_length].rstrip('_.')}__{digest}"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
