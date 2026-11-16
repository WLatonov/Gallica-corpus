from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

UNKNOWN_NEWSPAPERS = {
    "Journal des arts et de la politique",
    "Journal de la Côte-d'Or : feuille politique, littéraire, commerciale et industrielle...",
    "Le Réveil : journal des sciences, de la littérature, des moeurs, théâtres et beaux-arts",
    "Affiches, annonces et avis divers de la ville de Dijon",
    "Affiches, annonces et avis divers de la ville de Metz",
    "Feuille d'annonces du département de la Manche",
    "L'Ami de la Charte : journal du Puy-de-Dôme [\"puis\" du Puy-de-Dôme, de la Haute-Loire et du Cantal \"puis\" du Puy-de-Dôme, de la Haute-Loire, du Cantal et de la Corrèze]",
}

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def normalize_ark(value: str) -> str:
    return str(value).replace("ark:/12148/", "").strip("/")


def read_audit(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {row["newspaper"]: row["source_type"] for row in csv.DictReader(fh)}


def is_newspaper(title: str, source_types: dict[str, str]) -> bool:
    kind = source_types.get(title)
    return kind == "газета" or (kind is None and title in UNKNOWN_NEWSPAPERS)


def db_rows(db_path: Path, corpus: str, source_types: dict[str, str]) -> list[dict[str, object]]:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True)
    rows = con.execute(
        "SELECT d.issue_ark,d.page_number,c.title,c.publication_date "
        "FROM downloads d JOIN candidates c USING(issue_ark)"
    ).fetchall()
    con.close()
    out = []
    for issue_ark, page, title, date in rows:
        if not is_newspaper(str(title), source_types):
            continue
        ark = normalize_ark(issue_ark)
        out.append({
            "corpus": corpus,
            "newspaper": str(title),
            "publication_date": str(date)[:10],
            "issue_ark": ark,
            "digital_page": int(page),
            "issue_url": f"https://gallica.bnf.fr/ark:/12148/{ark}",
            "page_url": f"https://gallica.bnf.fr/ark:/12148/{ark}/f{int(page)}.item",
        })
    return out


def extra_rows(extra_root: Path, source_types: dict[str, str]) -> list[dict[str, object]]:
    out = []
    if not extra_root.exists():
        return out
    for path in extra_root.rglob("*_metadata.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8-sig"))
            title = str(meta["periodical_title"])
            if not is_newspaper(title, source_types):
                continue
            ark = normalize_ark(meta["issue_ark"])
            page = int(meta["digital_page"])
            out.append({
                "corpus": "personalized",
                "newspaper": title,
                "publication_date": str(meta["issue_date"])[:10],
                "issue_ark": ark,
                "digital_page": page,
                "issue_url": f"https://gallica.bnf.fr/ark:/12148/{ark}",
                "page_url": f"https://gallica.bnf.fr/ark:/12148/{ark}/f{page}.item",
            })
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return out


def deduplicate(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    unique = {}
    for row in rows:
        key = (str(row["issue_ark"]), int(row["digital_page"]))
        unique.setdefault(key, row)
    return sorted(
        unique.values(),
        key=lambda r: (str(r["publication_date"]), str(r["newspaper"]), str(r["issue_ark"]), int(r["digital_page"])),
    )


def save(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["corpus", "newspaper", "publication_date", "issue_ark", "digital_page", "issue_url", "page_url"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description="Экспорт полных списков страниц двух корпусов Gallica")
    ap.add_argument("--event-db", required=True)
    ap.add_argument("--personalized-db", required=True)
    ap.add_argument("--extra-personalized", required=True)
    ap.add_argument("--audit", default=str(project / "data" / "newspaper_type_audit.csv"))
    ap.add_argument("--output", default=str(project / "data"))
    args = ap.parse_args()

    source_types = read_audit(Path(args.audit))
    events = deduplicate(db_rows(Path(args.event_db), "event", source_types))
    personalized = deduplicate(
        db_rows(Path(args.personalized_db), "personalized", source_types)
        + extra_rows(Path(args.extra_personalized), source_types)
    )
    output = Path(args.output)
    save(output / "pages_event_corpus.csv", events)
    save(output / "pages_personalized_corpus.csv", personalized)

    event_keys = {(x["issue_ark"], x["digital_page"]) for x in events}
    personal_keys = {(x["issue_ark"], x["digital_page"]) for x in personalized}
    summary = {
        "event_pages": len(event_keys),
        "event_newspapers": len({x["newspaper"] for x in events}),
        "personalized_pages": len(personal_keys),
        "personalized_newspapers": len({x["newspaper"] for x in personalized}),
        "overlap_pages": len(event_keys & personal_keys),
        "combined_unique_pages": len(event_keys | personal_keys),
        "deduplication_key": "issue_ark + digital_page",
    }
    (output / "corpus_page_manifests_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
