from __future__ import annotations

import html
import logging
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime

from .models import Issue, SearchHit

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://gallica.bnf.fr"


class GallicaError(RuntimeError):
    pass


class GallicaClient:
    def __init__(
        self,
        *,
        delay_seconds: float = 1.5,
        timeout_seconds: float = 45,
        max_retries: int = 5,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._last_request_at = 0.0
        self.user_agent = "gallica-parser/0.1 (historical research; respectful API client)"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    def get(self, url: str, *, accept: str = "application/xml") -> bytes:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            # Count every request attempt, including failures, toward the rate limit.
            # Previously only successful responses advanced this timestamp, so a run
            # of 403/429/network errors could create an unintended request burst.
            self._last_request_at = time.monotonic()
            request = urllib.request.Request(
                url,
                headers={"User-Agent": self.user_agent, "Accept": accept},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    data = response.read()
                    content_type = response.headers.get("Content-Type", "")
                self._last_request_at = time.monotonic()
                if b"V\xc3\xa9rification de s\xc3\xa9curit\xc3\xa9" in data:
                    raise GallicaError("Gallica вернула страницу проверки безопасности")
                if accept == "application/xml" and "html" in content_type.casefold():
                    raise GallicaError(f"Вместо XML получен {content_type}")
                return data
            except urllib.error.HTTPError as exc:
                last_error = exc
                if 400 <= exc.code < 500 and exc.code != 429:
                    break
                if attempt == self.max_retries:
                    break
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                wait = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else min(60.0, (10 * (2 ** (attempt - 1))) + random.random())
                )
                LOGGER.warning("HTTP %s, повтор через %.1f с", exc.code, wait)
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, GallicaError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                wait = min(60.0, (2 ** (attempt - 1)) + random.random())
                LOGGER.warning("Ошибка запроса (%s), повтор через %.1f с", exc, wait)
                time.sleep(wait)
        raise GallicaError(f"Не удалось получить {url}: {last_error}") from last_error

    def list_issues(self, periodical_ark: str, year: int) -> list[Issue]:
        ark = f"ark:/12148/{periodical_ark}/date"
        url = f"{BASE_URL}/services/Issues?{urllib.parse.urlencode({'ark': ark, 'date': year})}"
        root = ET.fromstring(self.get(url))
        issues: list[Issue] = []
        for node in root.findall(".//issue"):
            issue_ark = node.attrib.get("ark")
            label = (node.text or "").strip()
            if not issue_ark or not label:
                continue
            try:
                publication_date = datetime.strptime(label, "%d %B %Y").date()
            except ValueError:
                publication_date = _date_from_day_of_year(year, node.attrib.get("dayOfYear"))
            issues.append(Issue(issue_ark, publication_date, label))
        return issues

    def search_issue(self, issue_ark: str, query: str) -> list[SearchHit]:
        url = f"{BASE_URL}/services/ContentSearch?{urllib.parse.urlencode({'ark': issue_ark, 'query': query})}"
        root = ET.fromstring(self.get(url))
        hits: list[SearchHit] = []
        for item in root.findall(".//item"):
            page_id = (item.findtext("p_id") or "").strip()
            match = re.search(r"(\d+)$", page_id)
            if not match:
                continue
            context = html.unescape(html.unescape(item.findtext("content") or ""))
            context = re.sub(r"<[^>]+>", "", context)
            context = re.sub(r"\s+", " ", context).strip()
            hits.append(SearchHit(issue_ark, query, int(match.group(1)), context))
        return hits

    def get_alto(self, issue_ark: str, page_number: int) -> bytes:
        params = urllib.parse.urlencode({"O": issue_ark, "E": "ALTO", "Deb": page_number})
        return self.get(f"{BASE_URL}/RequestDigitalElement?{params}")

    def get_page_image(self, issue_ark: str, page_number: int) -> bytes:
        base = f"{BASE_URL}/iiif/ark:/12148/{issue_ark}/f{page_number}/full/full/0"
        errors: list[str] = []
        for quality in ("native.jpg", "default.jpg"):
            try:
                return self.get(f"{base}/{quality}", accept="image/jpeg")
            except GallicaError as exc:
                errors.append(str(exc))
        raise GallicaError("; ".join(errors))


def _date_from_day_of_year(year: int, value: str | None) -> date:
    if value is None:
        raise GallicaError("У выпуска отсутствует дата")
    return datetime.strptime(f"{year}-{int(value):03d}", "%Y-%j").date()
