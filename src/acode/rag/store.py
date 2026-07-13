"""Convention store: the RAG backing for the coding agent.

Entries come in two kinds:

    rule     an executable tree-sitter rule (mechanically checkable) plus
             a human/LLM guideline, a good example, and a bad example.
             On insert, the rule is *self-verified*: it must flag the bad
             example and must not flag the good example. A convention
             that cannot be mechanically demonstrated is rejected.
    pattern  a canonical code snippet (e.g. indexed from the user's own
             codebase) used purely for retrieval, so generated code can
             be steered toward the user's existing shape.

Retrieval is a deterministic hybrid search engine:
    1. hard filter on language / kind / metadata (SQL + JSON match)
    2. rank by combining up to three signals — BM25 over a text query,
       AST-fingerprint cosine against query code, and metadata overlap.
       Ties break on id, so the same query always returns the same list
       in the same order.

The lexical and vector signals run on pluggable engines (see
engines.py): Tantivy and FAISS when installed — the leading open-source
engines for each role — with builtin fallbacks otherwise.

Storage is a single SQLite file — no external services.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..astcore.fingerprint import DIM, fingerprint_code
from ..astcore.parser import normalize_language, rule_languages
from ..astcore.rules import Rule, RuleEngine, RuleError, validate_rule
from .engines import (
    LexicalEngine,
    VectorEngine,
    create_lexical_engine,
    create_vector_engine,
)
from .textindex import document_text

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conventions (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('rule', 'pattern')),
    language    TEXT NOT NULL,
    title       TEXT NOT NULL,
    guideline   TEXT NOT NULL DEFAULT '',
    metadata    TEXT NOT NULL DEFAULT '{}',
    rule        TEXT,
    good_example TEXT NOT NULL DEFAULT '',
    bad_example  TEXT NOT NULL DEFAULT '',
    fingerprint BLOB,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conventions_lang ON conventions (language, kind);
"""


def _pack(vec: list[float] | None) -> bytes | None:
    if vec is None:
        return None
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


@dataclass
class Convention:
    id: str
    kind: str  # 'rule' | 'pattern'
    language: str
    title: str
    guideline: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    rule: Rule | None = None
    good_example: str = ""
    bad_example: str = ""
    fingerprint: list[float] | None = None

    def to_dict(self, include_fingerprint: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "language": self.language,
            "title": self.title,
            "guideline": self.guideline,
            "metadata": self.metadata,
            "rule": self.rule.to_dict() if self.rule else None,
            "good_example": self.good_example,
            "bad_example": self.bad_example,
        }
        if include_fingerprint:
            data["fingerprint"] = self.fingerprint
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Convention":
        rule_data = data.get("rule")
        rule = Rule.from_dict(rule_data) if rule_data else None
        return cls(
            id=data["id"],
            kind=data.get("kind", "rule" if rule else "pattern"),
            language=data["language"],
            title=data.get("title", data["id"]),
            guideline=data.get("guideline", ""),
            metadata=data.get("metadata", {}),
            rule=rule,
            good_example=data.get("good_example", ""),
            bad_example=data.get("bad_example", ""),
            fingerprint=data.get("fingerprint"),
        )


@dataclass
class SearchHit:
    convention: Convention
    score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        data = self.convention.to_dict()
        data["score"] = round(self.score, 6)
        data["match_reason"] = self.reason
        return data


def _metadata_matches(entry_meta: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Hard filter with wildcard semantics: an entry that doesn't declare a
    requested key matches any value for it (a generic no-print rule applies
    to fastapi projects too); an entry that does declare it must agree."""
    for key, wanted in filters.items():
        if wanted is None:
            continue
        have = entry_meta.get(key)
        if have is None:
            continue  # entry doesn't constrain this key -> applies everywhere
        if isinstance(wanted, (list, tuple, set)):
            have_set = set(have) if isinstance(have, list) else {have}
            if not set(wanted) & have_set:
                return False
        elif isinstance(have, list):
            if wanted not in have:
                return False
        elif have != wanted:
            return False
    return True


def _metadata_overlap(entry_meta: dict[str, Any], query_meta: dict[str, Any]) -> float:
    """Soft score in [0, 1]: fraction of query metadata the entry *declares
    and* matches. Unlike the hard filter, an absent key earns no score —
    wildcard entries pass the filter but rank below exact matches."""
    if not query_meta:
        return 0.0
    hits = 0
    for key, wanted in query_meta.items():
        if wanted is None:
            continue
        if entry_meta.get(key) is not None and _metadata_matches(entry_meta, {key: wanted}):
            hits += 1
    considered = sum(1 for v in query_meta.values() if v is not None)
    return hits / considered if considered else 0.0


class ConventionStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._engine = RuleEngine()
        # search engines, rebuilt lazily after writes
        self._lexical: LexicalEngine | None = None
        self._vector: VectorEngine | None = None

    def close(self) -> None:
        self._conn.close()

    def _invalidate_index(self) -> None:
        self._lexical = None
        self._vector = None

    def lexical_engine(self) -> LexicalEngine:
        """Full-text engine over all conventions (built lazily)."""
        if self._lexical is None:
            engine = create_lexical_engine()
            engine.build([(conv.id, document_text(conv)) for conv in self.list()])
            self._lexical = engine
        return self._lexical

    def vector_engine(self) -> VectorEngine:
        """AST-fingerprint similarity engine (built lazily)."""
        if self._vector is None:
            engine = create_vector_engine()
            engine.build([
                (conv.id, conv.fingerprint)
                for conv in self.list()
                if conv.fingerprint and len(conv.fingerprint) == DIM
            ])
            self._vector = engine
        return self._vector

    # ------------------------------------------------------------- write

    def add(self, convention: Convention, replace: bool = False) -> Convention:
        convention.language = normalize_language(convention.language)
        if convention.kind not in ("rule", "pattern"):
            raise ValueError(f"kind must be 'rule' or 'pattern', got {convention.kind!r}")
        if convention.kind == "rule":
            if convention.rule is None:
                raise RuleError(f"convention {convention.id!r} has kind=rule but no rule")
            convention.rule.language = convention.language
            self._self_verify(convention)
        elif not convention.good_example:
            raise ValueError("pattern conventions need a good_example snippet")

        anchor = convention.good_example or convention.bad_example
        if anchor:
            convention.fingerprint = fingerprint_code(anchor, convention.language)

        sql = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
        try:
            self._conn.execute(
                f"{sql} conventions "
                "(id, kind, language, title, guideline, metadata, rule, "
                " good_example, bad_example, fingerprint, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    convention.id,
                    convention.kind,
                    convention.language,
                    convention.title,
                    convention.guideline,
                    json.dumps(convention.metadata, ensure_ascii=False, sort_keys=True),
                    json.dumps(convention.rule.to_dict()) if convention.rule else None,
                    convention.good_example,
                    convention.bad_example,
                    _pack(convention.fingerprint),
                    time.time(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"convention {convention.id!r} already exists") from exc
        self._conn.commit()
        self._invalidate_index()
        return convention

    def _self_verify(self, convention: Convention) -> None:
        """A rule convention must be mechanically demonstrable on insert."""
        rule = convention.rule
        assert rule is not None
        validate_rule(rule)
        if convention.bad_example:
            report = self._engine.check(convention.bad_example, convention.language, [rule])
            if not report.violations:
                raise RuleError(
                    f"rule {rule.id!r} does not flag its own bad_example; "
                    "refusing to store an undemonstrated rule"
                )
        if convention.good_example:
            report = self._engine.check(convention.good_example, convention.language, [rule])
            if report.violations:
                raise RuleError(
                    f"rule {rule.id!r} flags its own good_example: "
                    f"{report.violations[0].message}"
                )

    def delete(self, convention_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM conventions WHERE id = ?", (convention_id,))
        self._conn.commit()
        self._invalidate_index()
        return cur.rowcount > 0

    # -------------------------------------------------------------- read

    def get(self, convention_id: str) -> Convention | None:
        row = self._conn.execute(
            "SELECT id, kind, language, title, guideline, metadata, rule, "
            "good_example, bad_example, fingerprint FROM conventions WHERE id = ?",
            (convention_id,),
        ).fetchone()
        return self._row_to_convention(row) if row else None

    def list(self, language: str | None = None, kind: str | None = None) -> list[Convention]:
        sql = (
            "SELECT id, kind, language, title, guideline, metadata, rule, "
            "good_example, bad_example, fingerprint FROM conventions WHERE 1=1"
        )
        params: list[Any] = []
        if language:
            # a dialect (tsx) also inherits its base language's conventions
            langs = rule_languages(language)
            sql += f" AND language IN ({', '.join('?' * len(langs))})"
            params.extend(langs)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_convention(row) for row in rows]

    def _row_to_convention(self, row: tuple) -> Convention:
        (id_, kind, language, title, guideline, metadata, rule_json,
         good, bad, fp) = row
        return Convention(
            id=id_,
            kind=kind,
            language=language,
            title=title,
            guideline=guideline,
            metadata=json.loads(metadata),
            rule=Rule.from_dict(json.loads(rule_json)) if rule_json else None,
            good_example=good,
            bad_example=bad,
            fingerprint=_unpack(fp),
        )

    # ------------------------------------------------------------ search

    def search(
        self,
        language: str,
        metadata: dict[str, Any] | None = None,
        code: str | None = None,
        query: str | None = None,
        kind: str | None = None,
        top_k: int = 8,
        min_similarity: float = 0.0,
    ) -> list[SearchHit]:
        """Hybrid retrieval: hard metadata filter, then a weighted blend
        of the available ranking signals —

            query (text)  BM25 over the inverted index      weight 0.35
            code (AST)    fingerprint cosine similarity     weight 0.45
            metadata      soft overlap score                weight 0.20

        Weights renormalize over the signals actually provided (e.g.
        text-only search ranks 0.64 * bm25 + 0.36 * metadata). With a
        text query and no code, entries with zero lexical match are
        dropped, like a search engine. Deterministic: same corpus +
        same query = same order (ties break on id).
        """
        language = normalize_language(language)
        metadata = metadata or {}
        candidates = self.list(language=language, kind=kind)
        candidates = [
            c for c in candidates if _metadata_matches(c.metadata, metadata)
        ]

        query_fp = fingerprint_code(code, language) if code else None
        ast_scores = self.vector_engine().similarities(query_fp) if query_fp else None
        text_scores = self.lexical_engine().scores(query) if query else None

        weights: dict[str, float] = {"meta": 0.20}
        if ast_scores is not None:
            weights["ast"] = 0.45
        if text_scores is not None:
            weights["text"] = 0.35
        total_weight = sum(weights.values())

        hits: list[SearchHit] = []
        for conv in candidates:
            parts: dict[str, float] = {
                "meta": _metadata_overlap(conv.metadata, metadata)
            }
            if ast_scores is not None:
                ast_score = ast_scores.get(conv.id, 0.0)
                if ast_score < min_similarity:
                    continue
                parts["ast"] = ast_score
            if text_scores is not None:
                parts["text"] = text_scores.get(conv.id, 0.0)
                if parts["text"] == 0.0 and query_fp is None:
                    continue  # pure text search: no lexical match, no hit
            score = sum(weights[k] * parts[k] for k in weights) / total_weight
            reason = ", ".join(
                f"{name}={parts[k]:.3f}" for k, name in (
                    ("text", "bm25"), ("ast", "ast_similarity"),
                    ("meta", "metadata_overlap"))
                if k in parts
            )
            hits.append(SearchHit(convention=conv, score=score, reason=reason))

        hits.sort(key=lambda h: (-h.score, h.convention.id))
        return hits[:top_k]

    # ---------------------------------------------------------- import/export

    def import_file(self, path: str | Path, replace: bool = False) -> list[str]:
        """Load conventions from a JSON file (a list or a single object)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else [data]
        added = []
        for entry in entries:
            conv = Convention.from_dict(entry)
            self.add(conv, replace=replace)
            added.append(conv.id)
        return added

    def export_all(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.list()]
