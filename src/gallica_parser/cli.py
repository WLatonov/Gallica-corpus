from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .parser import GallicaParser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gallica-parser",
        description="Поиск и выгрузка страниц исторической прессы из Gallica",
    )
    parser.add_argument("--config", default="config.json", help="путь к JSON-конфигурации")
    parser.add_argument("--max-issues", type=int, help="обработать не более N выпусков")
    parser.add_argument("--dry-run", action="store_true", help="получить только список выпусков")
    parser.add_argument("--no-resume", action="store_true", help="повторить выполненные запросы")
    parser.add_argument("--verbose", action="store_true", help="подробный журнал")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Некоторые Windows-окружения Python наследуют cp1252 даже в русской консоли.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    try:
        config = load_config(Path(args.config))
        worker = GallicaParser(config, resume=not args.no_resume)
        try:
            result = worker.run(max_issues=args.max_issues, dry_run=args.dry_run)
        finally:
            worker.close()
    except (OSError, ValueError, RuntimeError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1
    print(
        f"Готово: выпусков {result['issues']}, совпадений {result['hits']}, "
        f"скачано страниц {result['pages']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
