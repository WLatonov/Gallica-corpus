import csv
import json
import sqlite3
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
project = Path(os.environ.get("GALLICA_PARSER_ROOT", PROJECT))
corpus = Path(os.environ.get("GALLICA_EXTRA_CORPUS_ROOT", project / "corpus_alexandre_sainte_alliance_1812_1825"))
try:
    db = sqlite3.connect(project / "state_alexandre_sainte_alliance_1812_1825.sqlite3")
    db.execute("select count(1) from downloads").fetchone()
except sqlite3.OperationalError:
    db = sqlite3.connect(project / "state_alexandre_sainte_alliance_1812_1825.sqlite3")

db_rows = set(db.execute("select issue_ark, page_number from downloads"))
metadata_rows = {}
for path in corpus.rglob("page_*_metadata.json"):
    if "_superseded_collision_backup" in path.parts:
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    key = (data["issue_ark"].removeprefix("ark:/12148/"), int(data["digital_page"]))
    metadata_rows.setdefault(key, []).append(path)

print("db_rows", len(db_rows))
print("metadata_keys", len(metadata_rows))
print("db_without_metadata", sorted(db_rows - metadata_rows.keys()))
print("metadata_without_db", sorted(metadata_rows.keys() - db_rows))
print("duplicate_metadata", {k: [str(p) for p in v] for k, v in metadata_rows.items() if len(v) > 1})
print("query_errors", db.execute("select count(1) from issue_queries where status='error'").fetchone()[0])
print("completed_queries", db.execute("select count(1) from issue_queries where status='complete'").fetchone()[0])
print("errors", db.execute("select publication_date,issue_ark,query,error from issue_queries where status='error' order by publication_date").fetchall())
