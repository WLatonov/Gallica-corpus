from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Config:
    periodical_ark: str
    periodical_title: str
    date_from: date
    date_to: date
    queries: tuple[str, ...]
    context_require_any: tuple[str, ...]
    query_context_require_any: dict[str, tuple[str, ...]]
    output_directory: Path
    database_path: Path
    request_delay_seconds: float = 1.5
    request_timeout_seconds: float = 45.0
    max_retries: int = 5
    download_images: bool = True
    download_alto: bool = True


def _required(data: dict[str, Any], name: str) -> Any:
    if name not in data:
        raise ValueError(f"В конфигурации отсутствует обязательное поле: {name}")
    return data[name]


def load_config(path: str | Path) -> Config:
    config_path = Path(path).resolve()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent

    date_from = date.fromisoformat(_required(data, "date_from"))
    date_to = date.fromisoformat(_required(data, "date_to"))
    if date_from > date_to:
        raise ValueError("date_from не может быть позже date_to")

    queries = tuple(q.strip() for q in _required(data, "queries") if q.strip())
    if not queries:
        raise ValueError("Список queries не может быть пустым")

    ark = str(_required(data, "periodical_ark")).strip()
    if ark.startswith("ark:/12148/"):
        ark = ark.removeprefix("ark:/12148/")
    if ark.endswith("/date"):
        ark = ark.removesuffix("/date")

    return Config(
        periodical_ark=ark,
        periodical_title=str(data.get("periodical_title", ark)).strip(),
        date_from=date_from,
        date_to=date_to,
        queries=queries,
        context_require_any=tuple(
            str(term).casefold() for term in data.get("context_require_any", [])
        ),
        query_context_require_any={
            str(query).casefold(): tuple(str(term).casefold() for term in terms)
            for query, terms in data.get("query_context_require_any", {}).items()
        },
        output_directory=(base / data.get("output_directory", "data")).resolve(),
        database_path=(base / data.get("database_path", "state.sqlite3")).resolve(),
        request_delay_seconds=max(0.0, float(data.get("request_delay_seconds", 1.5))),
        request_timeout_seconds=max(1.0, float(data.get("request_timeout_seconds", 45))),
        max_retries=max(1, int(data.get("max_retries", 5))),
        download_images=bool(data.get("download_images", True)),
        download_alto=bool(data.get("download_alto", True)),
    )
