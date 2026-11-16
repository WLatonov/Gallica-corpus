import csv
import json
import sqlite3
import os
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
project = Path(os.environ.get("GALLICA_PARSER_ROOT", PROJECT))
corpus = Path(os.environ.get("GALLICA_EXTRA_CORPUS_ROOT", project / "corpus_alexandre_sainte_alliance_1812_1825"))
output = Path(os.environ.get("GALLICA_MANIFEST_OUT", PROJECT / "outputs" / "manifest"))
output.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(Path(os.environ.get("GALLICA_SINGLE_DB", project / "state_alexandre_sainte_alliance_1812_1825.sqlite3")))

items = []
missing_images = 0
for path in corpus.rglob("page_*_metadata.json"):
    if "_superseded_collision_backup" in path.parts:
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    if "page_image_error" in data.get("files_sha256", {}):
        missing_images += 1
    items.append((data, path))

items.sort(key=lambda item: (item[0]["issue_date"], item[0]["issue_ark"], item[0]["digital_page"]))
manifest_path = output / "manifest.csv"
with manifest_path.open("w", encoding="utf-8-sig", newline="") as stream:
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "periodical_title", "issue_date", "issue_ark", "page_number",
            "queries", "contexts", "local_directory", "page_url",
        ],
    )
    writer.writeheader()
    for data, path in items:
        hits = data.get("hits", [])
        writer.writerow(
            {
                "periodical_title": data["periodical_title"],
                "issue_date": data["issue_date"],
                "issue_ark": data["issue_ark"].removeprefix("ark:/12148/"),
                "page_number": data["digital_page"],
                "queries": " | ".join(sorted({hit["query"] for hit in hits})),
                "contexts": " | ".join(hit["context"] for hit in hits),
                "local_directory": str(path.parent),
                "page_url": data["page_url"],
            }
        )

by_year = Counter(data["issue_date"][:4] for data, _ in items)
summary = {
    "periodical": "Journal de la Lozère",
    "period": {"from": "1812-01-01", "to": "1825-12-31"},
    "available_issues_processed": 922,
    "queries_completed": db.execute(
        "select count(1) from issue_queries where status='complete'"
    ).fetchone()[0],
    "query_errors": db.execute(
        "select count(1) from issue_queries where status='error'"
    ).fetchone()[0],
    "raw_search_hits": db.execute("select count(1) from search_hits").fetchone()[0],
    "raw_hits_by_query": dict(
        db.execute("select query,count(1) from search_hits group by query").fetchall()
    ),
    "relevant_unique_pages": len(items),
    "pages_with_jpg": len(items) - missing_images,
    "pages_without_jpg_but_with_ocr": missing_images,
    "relevant_pages_by_year": dict(sorted(by_year.items())),
    "output_directory": str(corpus),
}
(output / "corpus_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
