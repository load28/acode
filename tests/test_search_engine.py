import json

from acode.rag.corpus import build_corpus, corpus_stats
from acode.rag.store import Convention, ConventionStore
from acode.rag.textindex import BM25Index, tokenize
from tests.conftest import REPO_ROOT


class TestTokenizer:
    def test_splits_snake_and_camel(self):
        assert tokenize("getUserById load_user") == [
            "get", "user", "by", "id", "load", "user"]

    def test_lowercase_and_short_dropped(self):
        assert tokenize("A BM25Index!") == ["bm25", "index"]


class TestBM25:
    def _index(self):
        return BM25Index.build([
            ("logging", "use logger instead of print logging output"),
            ("naming", "function names must be snake case naming"),
            ("routes", "fastapi route handler http endpoint"),
        ])

    def test_relevance(self):
        scores = self._index().scores("print logging")
        assert scores["logging"] > scores.get("naming", 0.0)

    def test_no_match_is_empty(self):
        assert self._index().scores("kubernetes") == {}

    def test_deterministic(self):
        a = self._index().scores("route http")
        b = self._index().scores("route http")
        assert a == b

    def test_normalized_max_is_one(self):
        normalized = self._index().normalized_scores("logging print")
        assert max(normalized.values()) == 1.0


class TestHybridSearch:
    def test_text_query_ranks_lexical_match_first(self, seeded_store):
        hits = seeded_store.search("python", query="print logging forbidden")
        assert hits[0].convention.id == "py-no-print"
        assert "bm25" in hits[0].reason

    def test_pure_text_search_drops_zero_matches(self, seeded_store):
        hits = seeded_store.search("python", query="print")
        ids = {h.convention.id for h in hits}
        assert "py-no-print" in ids
        assert len(ids) < len(seeded_store.list(language="python"))

    def test_text_plus_ast_combines(self, seeded_store):
        code = ("@router.get('/x')\nasync def h(db=Depends(get_db)):\n"
                "    return await db.get()\n")
        hits = seeded_store.search("python", query="route handler", code=code)
        assert hits[0].convention.id == "py-pattern-fastapi-route"
        assert "bm25" in hits[0].reason and "ast_similarity" in hits[0].reason

    def test_index_invalidated_on_add(self, store):
        store.add(Convention(id="p1", kind="pattern", language="python",
                             title="widget factory",
                             good_example="def make_widget():\n    \"\"\"w\"\"\"\n    return 1\n"))
        assert store.search("python", query="widget") != []
        store.add(Convention(id="p2", kind="pattern", language="python",
                             title="gadget builder",
                             good_example="def make_gadget():\n    \"\"\"g\"\"\"\n    return 2\n"))
        hits = store.search("python", query="gadget")
        assert [h.convention.id for h in hits] == ["p2"]

    def test_search_without_signals_still_works(self, seeded_store):
        # metadata-only path unchanged
        hits = seeded_store.search("python", metadata={"category": "logging"})
        assert [h.convention.id for h in hits] == ["py-no-print"]


class TestCorpusBuild:
    def test_build_from_repo_sources(self, tmp_path):
        db = tmp_path / "corpus.db"
        report = build_corpus(
            db,
            conventions_dir=REPO_ROOT / "conventions",
            index_paths=[REPO_ROOT / "src" / "acode" / "rag"],
        )
        assert report["errors"] == []
        assert report["conventions_loaded"] >= 21
        assert report["patterns_indexed"] > 0
        assert report["total_entries"] == (
            report["conventions_loaded"] + report["patterns_indexed"])

        # the built corpus is actually queryable
        store = ConventionStore(db)
        hits = store.search("python", query="bm25 inverted index")
        assert hits and "BM25Index" in hits[0].convention.title
        stats = corpus_stats(store)
        assert stats["bm25_terms"] > 100
        assert stats["rules"] >= 12

    def test_rebuild_is_fresh(self, tmp_path):
        db = tmp_path / "corpus.db"
        build_corpus(db, conventions_dir=REPO_ROOT / "conventions")
        first = build_corpus(db, conventions_dir=REPO_ROOT / "conventions")
        second = build_corpus(db, conventions_dir=REPO_ROOT / "conventions")
        assert first["total_entries"] == second["total_entries"]

    def test_broken_file_reported_not_fatal(self, tmp_path):
        conv_dir = tmp_path / "conventions"
        conv_dir.mkdir()
        (conv_dir / "broken.json").write_text(json.dumps([{
            "id": "broken-rule", "kind": "rule", "language": "python",
            "title": "broken",
            "rule": {"id": "broken-rule", "language": "python",
                     "type": "forbid", "query": "(call) @c", "message": "m"},
            "bad_example": "x = 1\n",  # rule cannot flag this -> self-verify fails
        }]), encoding="utf-8")
        (conv_dir / "ok.json").write_text(json.dumps([{
            "id": "ok-pattern", "kind": "pattern", "language": "python",
            "title": "ok", "good_example": "def f():\n    \"\"\"d\"\"\"\n    return 1\n",
        }]), encoding="utf-8")
        report = build_corpus(tmp_path / "c.db", conventions_dir=conv_dir)
        assert len(report["errors"]) == 1 and "broken" in report["errors"][0]
        assert report["conventions_loaded"] == 1


async def test_mcp_search_accepts_text_query(seeded_store):
    from acode.config import AcodeConfig
    from acode.mcpserver.server import build_server

    server = build_server(AcodeConfig(), seeded_store)
    result, _ = await server.call_tool("search_conventions", {
        "language": "python", "query": "mutable default argument"})
    hits = json.loads(result[0].text)
    assert hits[0]["id"] == "py-no-mutable-default"
