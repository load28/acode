"""Corpus lifecycle: build a real, queryable RAG database from sources.

A corpus has two source types, both re-runnable (so the corpus can be
updated later by just building again):

    convention JSON files   curated rules/patterns (self-verified on load)
    source trees            code indexed into `pattern` entries

``build_corpus`` assembles a fresh database from a conventions directory
plus any number of source paths, and reports exactly what went in. The
JSON files stay the source of truth in git; the SQLite file is a build
artifact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .indexer import index_codebase
from .store import ConventionStore


def build_corpus(
    db_path: str | Path,
    conventions_dir: str | Path | None = None,
    index_paths: list[str | Path] | None = None,
    fresh: bool = True,
    index_metadata: dict[str, Any] | None = None,
    max_files: int = 500,
) -> dict[str, Any]:
    """Build (or rebuild) the corpus database. Returns a build report."""
    db_path = Path(db_path)
    if fresh and db_path.exists() and str(db_path) != ":memory:":
        db_path.unlink()

    store = ConventionStore(db_path)
    report: dict[str, Any] = {
        "db_path": str(db_path),
        "convention_files": [],
        "conventions_loaded": 0,
        "indexed_paths": [],
        "patterns_indexed": 0,
        "errors": [],
    }

    if conventions_dir is not None:
        conventions_dir = Path(conventions_dir)
        json_files = sorted(conventions_dir.glob("*.json"))
        if not json_files:
            report["errors"].append(f"no *.json files in {conventions_dir}")
        for path in json_files:
            try:
                added = store.import_file(path, replace=True)
            except Exception as exc:  # a broken rule must not sink the build
                report["errors"].append(f"{path.name}: {exc}")
                continue
            report["convention_files"].append(
                {"file": str(path), "loaded": len(added), "ids": added})
            report["conventions_loaded"] += len(added)

    for path in index_paths or []:
        result = index_codebase(store, path, metadata=index_metadata,
                                max_files=max_files)
        report["indexed_paths"].append({
            "path": str(path),
            "files": result["files"],
            "patterns": len(result["indexed"]),
            "skipped": result["skipped"],
        })
        report["patterns_indexed"] += len(result["indexed"])

    report["total_entries"] = len(store.list())
    report["stats"] = corpus_stats(store)
    store.close()
    return report


def corpus_stats(store: ConventionStore) -> dict[str, Any]:
    """Corpus composition + search-index stats (for `acode corpus stats`)."""
    entries = store.list()
    by_language: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for conv in entries:
        by_language[conv.language] = by_language.get(conv.language, 0) + 1
        by_kind[conv.kind] = by_kind.get(conv.kind, 0) + 1
    index = store.text_index()
    return {
        "entries": len(entries),
        "by_language": dict(sorted(by_language.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "rules": by_kind.get("rule", 0),
        "patterns": by_kind.get("pattern", 0),
        "bm25_terms": len(index.postings),
        "bm25_avg_doc_len": round(index.avg_len, 1),
    }
