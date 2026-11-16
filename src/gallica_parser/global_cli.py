from __future__ import annotations

import argparse
import logging
import sys

from .global_parser import GlobalGallicaParser, load_global_config


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Глобальный сбор газет Gallica")
    parser.add_argument("--config", default="config_global_1812_1825.json")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--process-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    worker = GlobalGallicaParser(load_global_config(args.config))
    try:
        if not args.process_only:
            count = worker.discover()
            print(f"Найдено кандидатов: {count}")
        if not args.discover_only:
            result = worker.process_candidates(limit=args.limit)
            print(f"Обработано кандидатов: {result['candidates']}; скачано страниц: {result['saved']}")
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
