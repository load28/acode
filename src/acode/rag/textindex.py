"""Deterministic BM25 text index over conventions.

This is the lexical half of the hybrid search engine: a plain inverted
index with BM25 ranking, built in-process from the convention store. No
embedding model, no external service — the same corpus and query always
produce the same scores, so ranking stays auditable like everything
else in acode.

Indexed text per convention: id, title, guideline, metadata values,
rule message, and code tokens from the examples (identifiers are split
on snake_case/camelCase so `getUserById` matches "user").
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

_SPLIT = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for chunk in _SPLIT.split(text):
        if not chunk:
            continue
        for part in _CAMEL.split(chunk):
            part = part.lower()
            if len(part) >= 2:
                tokens.append(part)
    return tokens


def _flatten_metadata(metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in sorted(metadata.items()):
        parts.append(str(key))
        if isinstance(value, (list, tuple)):
            parts.extend(str(v) for v in value)
        else:
            parts.append(str(value))
    return " ".join(parts)


def document_text(conv: Any) -> str:
    """Searchable text for a Convention (duck-typed to avoid a cycle)."""
    parts = [
        conv.id,
        conv.title,
        conv.guideline,
        conv.language,
        conv.kind,
        _flatten_metadata(conv.metadata or {}),
    ]
    if conv.rule is not None:
        parts.append(conv.rule.message)
    if conv.good_example:
        parts.append(conv.good_example)
    if conv.bad_example:
        parts.append(conv.bad_example)
    return "\n".join(p for p in parts if p)


@dataclass
class BM25Index:
    doc_count: int = 0
    avg_len: float = 0.0
    doc_lens: dict[str, int] = field(default_factory=dict)
    # term -> {doc_id: term_frequency}
    postings: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def build(cls, documents: Iterable[tuple[str, str]]) -> "BM25Index":
        """documents: iterable of (doc_id, text)."""
        index = cls()
        postings: dict[str, dict[str, int]] = defaultdict(dict)
        total_len = 0
        for doc_id, text in documents:
            tokens = tokenize(text)
            index.doc_lens[doc_id] = len(tokens)
            total_len += len(tokens)
            for term, tf in Counter(tokens).items():
                postings[term][doc_id] = tf
        index.postings = dict(postings)
        index.doc_count = len(index.doc_lens)
        index.avg_len = (total_len / index.doc_count) if index.doc_count else 0.0
        return index

    def _idf(self, term: str) -> float:
        df = len(self.postings.get(term, ()))
        if df == 0:
            return 0.0
        return math.log(1.0 + (self.doc_count - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> dict[str, float]:
        """BM25 score per doc for the query. Deterministic."""
        accum: dict[str, float] = defaultdict(float)
        for term in tokenize(query):
            idf = self._idf(term)
            if idf == 0.0:
                continue
            for doc_id, tf in self.postings[term].items():
                dl = self.doc_lens[doc_id]
                denom = tf + K1 * (1 - B + B * dl / self.avg_len if self.avg_len else 1.0)
                accum[doc_id] += idf * (tf * (K1 + 1)) / denom
        return dict(accum)

    def normalized_scores(self, query: str) -> dict[str, float]:
        """Scores scaled to [0, 1] by the best match (empty if no match)."""
        raw = self.scores(query)
        if not raw:
            return {}
        best = max(raw.values())
        if best <= 0:
            return {}
        return {doc_id: score / best for doc_id, score in raw.items()}
