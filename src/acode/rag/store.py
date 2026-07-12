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

Retrieval is deterministic:
    1. hard filter on language / kind / metadata (SQL + JSON match)
    2. rank by cosine similarity between the query code's AST
       fingerprint and each entry's stored fingerprint; without query
       code, rank by metadata overlap. Ties break on id, so the same
       query always returns the same list in the same order.

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

from ..astcore.fingerprint import DIM, cosine, fingerprint_code
from ..astcore.parser import normalize_language
from ..astcore.rules import Rule, RuleEngine, RuleError, validate_rule

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
    """Hard filter: every requested key must be satisfied by the entry."""
    for key, wanted in filters.items():
        if wanted is None:
            continue
        have = entry_meta.get(key)
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
    """Soft score in [0, 1]: fraction of query metadata the entry shares."""
    if not query_meta:
        return 0.0
    hits = 0
    for key, wanted in query_meta.items():
        if wanted is None:
            continue
        if _metadata_matches(entry_meta, {key: wanted}):
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

    def close(self) -> None:
        self._conn.close()

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
            sql += " AND language = ?"
            params.append(normalize_language(language))
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
        kind: str | None = None,
        top_k: int = 8,
        min_similarity: float = 0.0,
    ) -> list[SearchHit]:
        """Metadata-filtered, AST-similarity-ranked retrieval.

        With ``code``: rank = 0.7 * AST cosine + 0.3 * metadata overlap
        (modification path — find conventions whose examples look like
        the code being edited).
        Without ``code``: rank = metadata overlap (generation path).
        Deterministic: ties break on convention id.
        """
        language = normalize_language(language)
        metadata = metadata or {}
        candidates = self.list(language=language, kind=kind)
        candidates = [
            c for c in candidates if _metadata_matches(c.metadata, metadata)
        ]

        query_fp = fingerprint_code(code, language) if code else None
        hits: list[SearchHit] = []
        for conv in candidates:
            meta_score = _metadata_overlap(conv.metadata, metadata)
            if query_fp is not None and conv.fingerprint and len(conv.fingerprint) == DIM:
                ast_score = max(0.0, cosine(query_fp, conv.fingerprint))
                if ast_score < min_similarity:
                    continue
                score = 0.7 * ast_score + 0.3 * meta_score
                reason = f"ast_similarity={ast_score:.3f}, metadata_overlap={meta_score:.2f}"
            else:
                score = meta_score
                reason = f"metadata_overlap={meta_score:.2f}"
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
