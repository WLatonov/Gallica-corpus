from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from .alto import extract_alto_text
from .client import GallicaClient
from .config import Config
from .models import Issue, SearchHit
from .state import StateDatabase

LOGGER = logging.getLogger(__name__)


class GallicaParser:
    def __init__(self, config: Config, *, resume: bool = True) -> None:
        self.config = config
        self.resume = resume
        self.client = GallicaClient(
            delay_seconds=config.request_delay_seconds,
            timeout_seconds=config.request_timeout_seconds,
            max_retries=config.max_retries,
        )
        self.state = StateDatabase(config.database_path)

    def close(self) -> None:
        self.state.close()

    def collect_issues(self) -> list[Issue]:
        issues: dict[str, Issue] = {}
        for year in range(self.config.date_from.year, self.config.date_to.year + 1):
            LOGGER.info("Получение выпусков за %d год", year)
            for issue in self.client.list_issues(self.config.periodical_ark, year):
                if self.config.date_from <= issue.publication_date <= self.config.date_to:
                    issues[issue.ark] = issue
        return sorted(issues.values(), key=lambda item: (item.publication_date, item.ark))

    def run(self, *, max_issues: int | None = None, dry_run: bool = False) -> dict[str, int]:
        issues = self.collect_issues()
        if max_issues is not None:
            issues = issues[:max_issues]
        LOGGER.info("В интервале найдено выпусков: %d", len(issues))
        if dry_run:
            return {"issues": len(issues), "hits": 0, "pages": 0}

        total_hits = 0
        downloaded_pages = 0
        for position, issue in enumerate(issues, 1):
            LOGGER.info("[%d/%d] %s (%s)", position, len(issues), issue.label, issue.ark)
            hits_by_page: dict[int, list[SearchHit]] = defaultdict(list)
            for query in self.config.queries:
                if self.resume and self.state.is_completed(issue.ark, query):
                    LOGGER.debug("Пропуск ранее выполненного запроса: %s", query)
                    hits = self.state.get_hits(issue.ark, query)
                else:
                    try:
                        hits = self.client.search_issue(issue.ark, query)
                        self.state.record_query(issue, query, hits)
                    except Exception as exc:
                        self.state.record_error(issue, query, str(exc))
                        LOGGER.error("Ошибка поиска %r: %s", query, exc)
                        continue
                for hit in hits:
                    if self._context_matches(query, hit.context):
                        hits_by_page[hit.page_number].append(hit)
                        total_hits += 1

            for page_number, hits in sorted(hits_by_page.items()):
                if self.state.is_downloaded(issue.ark, page_number):
                    LOGGER.debug("Страница уже скачана: %s, %d", issue.ark, page_number)
                    continue
                try:
                    self._download_page(issue, page_number, hits)
                    downloaded_pages += 1
                except Exception as exc:
                    LOGGER.error(
                        "Не удалось сохранить страницу %s/f%d: %s",
                        issue.ark,
                        page_number,
                        exc,
                    )

        return {"issues": len(issues), "hits": total_hits, "pages": downloaded_pages}

    def _context_matches(self, query: str, context: str) -> bool:
        required = self.config.query_context_require_any.get(
            query.casefold(), self.config.context_require_any
        )
        if not required:
            return True
        folded = context.casefold()
        return any(term in folded for term in required)

    def _download_page(self, issue: Issue, page_number: int, hits: list[SearchHit]) -> None:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", self.config.periodical_title).strip("_")
        # У одной даты Gallica может иметь несколько цифровых копий выпуска.
        # ARK в пути предотвращает перезапись одноимённых страниц.
        target = (
            self.config.output_directory
            / slug
            / issue.publication_date.isoformat()
            / issue.ark
        )
        target.mkdir(parents=True, exist_ok=True)

        alto = self.client.get_alto(issue.ark, page_number)
        raw_text, clean_text = extract_alto_text(alto)
        files: dict[str, str] = {}

        if self.config.download_alto:
            alto_path = target / f"page_{page_number}_alto.xml"
            alto_path.write_bytes(alto)
            files[alto_path.name] = _sha256(alto)

        raw_path = target / f"page_{page_number}_ocr.txt"
        raw_path.write_text(raw_text, encoding="utf-8")
        files[raw_path.name] = _sha256(raw_text.encode("utf-8"))

        clean_path = target / f"page_{page_number}_ocr_clean.txt"
        clean_path.write_text(clean_text, encoding="utf-8")
        files[clean_path.name] = _sha256(clean_text.encode("utf-8"))

        if self.config.download_images:
            try:
                image = self.client.get_page_image(issue.ark, page_number)
                image_path = target / f"page_{page_number}.jpg"
                image_path.write_bytes(image)
                files[image_path.name] = _sha256(image)
            except Exception as exc:
                LOGGER.warning("Изображение страницы недоступно: %s", exc)
                files["page_image_error"] = str(exc)

        metadata = {
            "periodical_title": self.config.periodical_title,
            "periodical_ark": f"ark:/12148/{self.config.periodical_ark}",
            "issue_ark": f"ark:/12148/{issue.ark}",
            "issue_date": issue.publication_date.isoformat(),
            "digital_page": page_number,
            "issue_url": f"https://gallica.bnf.fr/ark:/12148/{issue.ark}",
            "page_url": f"https://gallica.bnf.fr/ark:/12148/{issue.ark}/f{page_number}.item",
            "hits": [asdict(hit) for hit in hits],
            "files_sha256": files,
        }
        metadata_path = target / f"page_{page_number}_metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._append_manifest(issue, page_number, hits, target)
        self.state.record_download(issue.ark, page_number, target)
        LOGGER.info("Сохранена страница %d: %s", page_number, target)

    def _append_manifest(
        self, issue: Issue, page_number: int, hits: list[SearchHit], target: Path
    ) -> None:
        manifest = self.config.output_directory / "manifest.csv"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        new_file = not manifest.exists()
        with manifest.open("a", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "periodical_title", "issue_date", "issue_ark", "page_number",
                    "queries", "contexts", "local_directory", "page_url",
                ],
            )
            if new_file:
                writer.writeheader()
            writer.writerow(
                {
                    "periodical_title": self.config.periodical_title,
                    "issue_date": issue.publication_date.isoformat(),
                    "issue_ark": issue.ark,
                    "page_number": page_number,
                    "queries": " | ".join(sorted({hit.query for hit in hits})),
                    "contexts": " | ".join(hit.context for hit in hits),
                    "local_directory": str(target),
                    "page_url": f"https://gallica.bnf.fr/ark:/12148/{issue.ark}/f{page_number}.item",
                }
            )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
