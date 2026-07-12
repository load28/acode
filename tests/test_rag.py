import pytest

from acode.astcore.rules import Rule, RuleError
from acode.rag.indexer import index_codebase
from acode.rag.store import Convention


def _rule_convention(cid="no-print", metadata=None):
    return Convention(
        id=cid, kind="rule", language="python", title="no print",
        guideline="use logging", metadata=metadata or {},
        rule=Rule(id=cid, language="python", type="forbid",
                  query='(call function: (identifier) @fn (#eq? @fn "print"))',
                  capture="fn", message="no print"),
        good_example="import logging\nlogging.getLogger(__name__).info('x')\n",
        bad_example="print('x')\n",
    )


class TestSelfVerification:
    def test_valid_rule_accepted(self, store):
        store.add(_rule_convention())
        assert store.get("no-print") is not None

    def test_rule_that_misses_bad_example_rejected(self, store):
        conv = _rule_convention()
        conv.bad_example = "x = 1\n"  # rule does not flag this
        with pytest.raises(RuleError, match="does not flag"):
            store.add(conv)

    def test_rule_that_flags_good_example_rejected(self, store):
        conv = _rule_convention()
        conv.good_example = "print('oops')\n"
        with pytest.raises(RuleError, match="flags its own good_example"):
            store.add(conv)

    def test_pattern_requires_snippet(self, store):
        with pytest.raises(ValueError):
            store.add(Convention(id="p", kind="pattern", language="python", title="t"))


class TestSearch:
    def test_metadata_hard_filter(self, store):
        store.add(_rule_convention("a", {"framework": "fastapi"}))
        store.add(_rule_convention("b", {"framework": "django"}))
        hits = store.search("python", metadata={"framework": "fastapi"})
        assert [h.convention.id for h in hits] == ["a"]

    def test_tag_list_filter(self, store):
        store.add(_rule_convention("a", {"tags": ["http", "api"]}))
        store.add(_rule_convention("b", {"tags": ["db"]}))
        hits = store.search("python", metadata={"tags": ["api"]})
        assert [h.convention.id for h in hits] == ["a"]

    def test_ast_similarity_ranking(self, store):
        store.add(Convention(
            id="pattern-route", kind="pattern", language="python", title="route",
            good_example=(
                "@app.get('/items')\nasync def list_items(db=Depends(get_db)):\n"
                "    return await db.fetch()\n"),
        ))
        store.add(Convention(
            id="pattern-config", kind="pattern", language="python", title="config",
            good_example="class Config:\n    DEBUG = False\n    NAME = 'x'\n",
        ))
        query_code = (
            "@app.post('/users')\nasync def create_user(db=Depends(get_db)):\n"
            "    return await db.insert()\n")
        hits = store.search("python", code=query_code)
        assert hits[0].convention.id == "pattern-route"

    def test_deterministic_ordering(self, store):
        for cid in ("b", "a", "c"):
            store.add(_rule_convention(cid))
        first = [h.convention.id for h in store.search("python")]
        second = [h.convention.id for h in store.search("python")]
        assert first == second == ["a", "b", "c"]

    def test_language_isolation(self, store):
        store.add(_rule_convention())
        assert store.search("typescript") == []

    def test_duplicate_id_rejected_without_replace(self, store):
        store.add(_rule_convention())
        with pytest.raises(ValueError, match="already exists"):
            store.add(_rule_convention())
        store.add(_rule_convention(), replace=True)  # ok


class TestIndexer:
    def test_index_python_file(self, store, tmp_path):
        src = tmp_path / "svc.py"
        src.write_text(
            "def load_user(user_id):\n"
            "    \"\"\"Load.\"\"\"\n"
            "    return repo.get(user_id)\n"
            "\n\n"
            "class UserService:\n"
            "    def __init__(self, repo):\n"
            "        self.repo = repo\n",
            encoding="utf-8",
        )
        result = index_codebase(store, tmp_path)
        assert result["files"] == 1
        indexed = store.list(kind="pattern")
        names = {c.metadata["unit"] for c in indexed}
        assert {"load_user", "UserService"} <= names
        assert all(c.fingerprint for c in indexed)

    def test_index_is_idempotent(self, store, tmp_path):
        src = tmp_path / "a.py"
        src.write_text("def f():\n    \"\"\"d\"\"\"\n    return 1\n", encoding="utf-8")
        index_codebase(store, tmp_path)
        count = len(store.list(kind="pattern"))
        index_codebase(store, tmp_path)
        assert len(store.list(kind="pattern")) == count


def test_seed_conventions_import(seeded_store):
    ids = {c.id for c in seeded_store.list()}
    assert "py-no-print" in ids and "ts-no-any" in ids
    # every seeded rule self-verified on insert (add() would have raised)
    assert all(c.rule for c in seeded_store.list(kind="rule"))
