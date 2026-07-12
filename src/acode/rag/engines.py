"""Pluggable search engines for the convention corpus.

Two engine roles, each with a leading open-source implementation and a
zero-dependency builtin fallback:

    lexical (text query, BM25)
        tantivy   Tantivy — the Rust successor to Lucene, the leading
                  embeddable full-text search engine (powers Quickwit)
        builtin   acode's own BM25 inverted index (textindex.py)

    vector (AST-fingerprint similarity)
        faiss     FAISS — Meta's industry-standard vector similarity
                  library; IndexFlatIP over L2-normalized fingerprints
                  gives *exact* cosine search, so determinism holds
        builtin   plain Python cosine loop

Selection is automatic (leading engine if importable, else builtin) and
can be pinned via ACODE_LEXICAL_ENGINE / ACODE_VECTOR_ENGINE
(auto | tantivy | builtin) / (auto | faiss | builtin).

Both roles keep the same contract: engines are rebuilt from the store
on demand (lazily, invalidated on writes) and must return deterministic
scores — same corpus + same query = same numbers.

Text fed to the lexical engines is pre-tokenized with acode's tokenizer
(snake_case/camelCase splitting) so `getUserById` matches "user"
regardless of backend.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from .textindex import BM25Index, tokenize


# --------------------------------------------------------------- lexical


class LexicalEngine(ABC):
    """Full-text engine: build from (doc_id, text) pairs, score a query."""

    name: str = "base"

    @abstractmethod
    def build(self, documents: list[tuple[str, str]]) -> None:
        ...

    @abstractmethod
    def scores(self, query: str) -> dict[str, float]:
        """Normalized scores in [0, 1] (best match = 1.0); {} on no match."""
        ...

    def stats(self) -> dict[str, object]:
        return {}


class BuiltinLexicalEngine(LexicalEngine):
    name = "builtin-bm25"

    def __init__(self) -> None:
        self._index = BM25Index()

    def build(self, documents: list[tuple[str, str]]) -> None:
        self._index = BM25Index.build(documents)

    def scores(self, query: str) -> dict[str, float]:
        return self._index.normalized_scores(query)

    def stats(self) -> dict[str, object]:
        return {
            "docs": self._index.doc_count,
            "terms": len(self._index.postings),
            "avg_doc_len": round(self._index.avg_len, 1),
        }


class TantivyLexicalEngine(LexicalEngine):
    name = "tantivy"

    @staticmethod
    def available() -> bool:
        try:
            import tantivy  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self) -> None:
        import tantivy

        self._tantivy = tantivy
        self._index = None
        self._doc_count = 0

    def build(self, documents: list[tuple[str, str]]) -> None:
        tantivy = self._tantivy
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("doc_id", stored=True)
        builder.add_text_field("body", stored=False)
        schema = builder.build()
        index = tantivy.Index(schema)  # in-RAM index
        # single writer thread -> one segment -> deterministic BM25 stats
        writer = index.writer(heap_size=15_000_000, num_threads=1)
        for doc_id, text in documents:
            writer.add_document(tantivy.Document(
                doc_id=doc_id, body=" ".join(tokenize(text))))
        writer.commit()
        index.reload()
        self._index = index
        self._doc_count = len(documents)

    def scores(self, query: str) -> dict[str, float]:
        if self._index is None or self._doc_count == 0:
            return {}
        terms = tokenize(query)
        if not terms:
            return {}
        searcher = self._index.searcher()
        parsed = self._index.parse_query(" ".join(terms), ["body"])
        hits = searcher.search(parsed, self._doc_count).hits
        raw: dict[str, float] = {}
        for score, address in hits:
            doc = searcher.doc(address)
            raw[doc["doc_id"][0]] = float(score)
        if not raw:
            return {}
        best = max(raw.values())
        if best <= 0:
            return {}
        return {doc_id: score / best for doc_id, score in raw.items()}

    def stats(self) -> dict[str, object]:
        return {"docs": self._doc_count}


# ---------------------------------------------------------------- vector


class VectorEngine(ABC):
    """Similarity engine over L2-normalized fingerprint vectors."""

    name: str = "base"

    @abstractmethod
    def build(self, items: list[tuple[str, list[float]]]) -> None:
        ...

    @abstractmethod
    def similarities(self, query_vector: list[float]) -> dict[str, float]:
        """Cosine similarity per doc id, clipped to [0, 1]."""
        ...


class BuiltinVectorEngine(VectorEngine):
    name = "builtin-cosine"

    def __init__(self) -> None:
        self._items: list[tuple[str, list[float]]] = []

    def build(self, items: list[tuple[str, list[float]]]) -> None:
        self._items = items

    def similarities(self, query_vector: list[float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for doc_id, vec in self._items:
            if len(vec) != len(query_vector):
                continue
            sim = sum(a * b for a, b in zip(query_vector, vec))
            result[doc_id] = min(1.0, max(0.0, sim))
        return result


class FaissVectorEngine(VectorEngine):
    name = "faiss"

    @staticmethod
    def available() -> bool:
        try:
            import faiss  # noqa: F401
            import numpy  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self) -> None:
        import faiss
        import numpy

        self._faiss = faiss
        self._np = numpy
        self._index = None
        self._ids: list[str] = []

    def build(self, items: list[tuple[str, list[float]]]) -> None:
        self._ids = [doc_id for doc_id, _ in items]
        if not items:
            self._index = None
            return
        dim = len(items[0][1])
        matrix = self._np.array([vec for _, vec in items], dtype="float32")
        # exact inner-product search; fingerprints are L2-normalized,
        # so inner product == cosine and results are deterministic
        index = self._faiss.IndexFlatIP(dim)
        index.add(matrix)
        self._index = index

    def similarities(self, query_vector: list[float]) -> dict[str, float]:
        if self._index is None or not self._ids:
            return {}
        query = self._np.array([query_vector], dtype="float32")
        distances, indices = self._index.search(query, len(self._ids))
        result: dict[str, float] = {}
        for sim, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            result[self._ids[idx]] = min(1.0, max(0.0, float(sim)))
        return result


# --------------------------------------------------------------- factory


def create_lexical_engine(preference: str | None = None) -> LexicalEngine:
    pref = (preference or os.environ.get("ACODE_LEXICAL_ENGINE", "auto")).lower()
    if pref == "tantivy":
        return TantivyLexicalEngine()
    if pref == "builtin":
        return BuiltinLexicalEngine()
    if pref != "auto":
        raise ValueError(f"unknown lexical engine {pref!r}; expected auto|tantivy|builtin")
    if TantivyLexicalEngine.available():
        return TantivyLexicalEngine()
    return BuiltinLexicalEngine()


def create_vector_engine(preference: str | None = None) -> VectorEngine:
    pref = (preference or os.environ.get("ACODE_VECTOR_ENGINE", "auto")).lower()
    if pref == "faiss":
        return FaissVectorEngine()
    if pref == "builtin":
        return BuiltinVectorEngine()
    if pref != "auto":
        raise ValueError(f"unknown vector engine {pref!r}; expected auto|faiss|builtin")
    if FaissVectorEngine.available():
        return FaissVectorEngine()
    return BuiltinVectorEngine()
