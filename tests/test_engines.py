"""Engine backends must be interchangeable: same corpus, same query,
same top results — whether the leading OSS engine or the builtin
fallback is running."""

import pytest

from acode.rag.engines import (
    BuiltinLexicalEngine,
    BuiltinVectorEngine,
    FaissVectorEngine,
    TantivyLexicalEngine,
    create_lexical_engine,
    create_vector_engine,
)

DOCS = [
    ("logging", "use logger instead of print logging output"),
    ("naming", "function names must be snake case naming"),
    ("routes", "fastapi route handler http endpoint getUserById"),
]

_lexical_backends = [BuiltinLexicalEngine]
if TantivyLexicalEngine.available():
    _lexical_backends.append(TantivyLexicalEngine)

_vector_backends = [BuiltinVectorEngine]
if FaissVectorEngine.available():
    _vector_backends.append(FaissVectorEngine)


@pytest.mark.parametrize("backend", _lexical_backends)
class TestLexicalBackends:
    def _engine(self, backend):
        engine = backend()
        engine.build(DOCS)
        return engine

    def test_relevance(self, backend):
        scores = self._engine(backend).scores("print logging")
        assert scores["logging"] > scores.get("naming", 0.0)

    def test_camel_case_matches(self, backend):
        scores = self._engine(backend).scores("user")
        assert "routes" in scores

    def test_no_match_empty(self, backend):
        assert self._engine(backend).scores("kubernetes") == {}

    def test_deterministic(self, backend):
        engine = self._engine(backend)
        assert engine.scores("route http") == engine.scores("route http")

    def test_normalized(self, backend):
        scores = self._engine(backend).scores("logging print")
        assert max(scores.values()) == 1.0

    def test_empty_corpus(self, backend):
        engine = backend()
        engine.build([])
        assert engine.scores("anything") == {}


@pytest.mark.parametrize("backend", _vector_backends)
class TestVectorBackends:
    def _engine(self, backend):
        from acode.astcore.fingerprint import fingerprint_code

        engine = backend()
        engine.build([
            ("route", fingerprint_code(
                "@app.get('/x')\nasync def h(db=Depends(g)):\n    return await db.f()\n",
                "python")),
            ("config", fingerprint_code(
                "class Config:\n    DEBUG = False\n    NAME = 'x'\n", "python")),
        ])
        return engine

    def test_similarity_ranking(self, backend):
        from acode.astcore.fingerprint import fingerprint_code

        query = fingerprint_code(
            "@app.post('/y')\nasync def k(db=Depends(g)):\n    return await db.i()\n",
            "python")
        sims = self._engine(backend).similarities(query)
        assert sims["route"] > sims["config"]
        assert 0.0 <= sims["config"] <= sims["route"] <= 1.0

    def test_self_similarity_is_one(self, backend):
        from acode.astcore.fingerprint import fingerprint_code

        vec = fingerprint_code("x = 1\n", "python")
        engine = backend()
        engine.build([("self", vec)])
        assert engine.similarities(vec)["self"] == pytest.approx(1.0, abs=1e-5)

    def test_empty_corpus(self, backend):
        engine = backend()
        engine.build([])
        assert engine.similarities([0.0] * 256) == {}


class TestBackendAgreement:
    """The leading engine and the fallback must produce the same ranking."""

    @pytest.mark.skipif(not TantivyLexicalEngine.available(),
                        reason="tantivy not installed")
    def test_lexical_rank_agreement(self):
        builtin, tantivy_engine = BuiltinLexicalEngine(), TantivyLexicalEngine()
        builtin.build(DOCS)
        tantivy_engine.build(DOCS)
        for query in ("print logging", "route http handler", "snake naming"):
            a = sorted(builtin.scores(query), key=lambda d: -builtin.scores(query)[d])
            b = sorted(tantivy_engine.scores(query),
                       key=lambda d: -tantivy_engine.scores(query)[d])
            assert a[:1] == b[:1], f"top result differs for {query!r}"

    @pytest.mark.skipif(not FaissVectorEngine.available(),
                        reason="faiss not installed")
    def test_vector_score_agreement(self):
        from acode.astcore.fingerprint import fingerprint_code

        items = [
            ("a", fingerprint_code("def f():\n    return 1\n", "python")),
            ("b", fingerprint_code("class C:\n    x = 1\n", "python")),
        ]
        query = fingerprint_code("def g():\n    return 2\n", "python")
        builtin, faiss_engine = BuiltinVectorEngine(), FaissVectorEngine()
        builtin.build(items)
        faiss_engine.build(items)
        a, b = builtin.similarities(query), faiss_engine.similarities(query)
        for key in a:
            assert a[key] == pytest.approx(b[key], abs=1e-5)


class TestFactory:
    def test_auto_prefers_leading_engines_when_installed(self):
        lexical = create_lexical_engine("auto")
        vector = create_vector_engine("auto")
        if TantivyLexicalEngine.available():
            assert lexical.name == "tantivy"
        if FaissVectorEngine.available():
            assert vector.name == "faiss"

    def test_builtin_can_be_forced(self):
        assert create_lexical_engine("builtin").name == "builtin-bm25"
        assert create_vector_engine("builtin").name == "builtin-cosine"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("ACODE_LEXICAL_ENGINE", "builtin")
        monkeypatch.setenv("ACODE_VECTOR_ENGINE", "builtin")
        assert create_lexical_engine().name == "builtin-bm25"
        assert create_vector_engine().name == "builtin-cosine"

    def test_unknown_engine_rejected(self):
        with pytest.raises(ValueError):
            create_lexical_engine("elasticsearch")


class TestStoreWithBothBackends:
    """End-to-end store search must behave the same on either stack."""

    @pytest.fixture(params=["builtin", "auto"])
    def engine_env(self, request, monkeypatch):
        if request.param == "builtin":
            monkeypatch.setenv("ACODE_LEXICAL_ENGINE", "builtin")
            monkeypatch.setenv("ACODE_VECTOR_ENGINE", "builtin")
        return request.param

    def test_hybrid_search(self, engine_env, seeded_store):
        seeded_store._invalidate_index()  # pick up the env-selected engines
        hits = seeded_store.search("python", query="print logging forbidden")
        assert hits[0].convention.id == "py-no-print"
        code = ("@router.get('/x')\nasync def h(db=Depends(get_db)):\n"
                "    return await db.get()\n")
        hits = seeded_store.search("python", code=code)
        assert hits[0].convention.id == "py-pattern-fastapi-route"
