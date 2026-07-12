import json

import pytest

from acode.config import AcodeConfig
from acode.mcpserver.server import build_server
from acode.rag.store import ConventionStore
from tests.conftest import REPO_ROOT


@pytest.fixture()
def server(seeded_store: ConventionStore):
    config = AcodeConfig()
    config.db_path = ":memory:"
    return build_server(config, seeded_store)


async def _call(server, tool, args):
    result, _ = await server.call_tool(tool, args)
    return json.loads(result[0].text)


class TestMcpTools:
    async def test_tools_registered(self, server):
        tools = {t.name for t in await server.list_tools()}
        assert {
            "search_conventions", "check_code", "generate_code", "review_code",
            "add_convention", "list_conventions", "delete_convention",
            "index_codebase", "server_info",
        } <= tools

    async def test_check_code_deterministic(self, server):
        args = {"language": "python", "code": "print('x')\n"}
        first = await _call(server, "check_code", args)
        second = await _call(server, "check_code", args)
        assert first == second
        assert not first["passed"]
        assert any(v["rule_id"] == "py-no-print" for v in first["violations"])

    async def test_check_code_no_llm_needed(self, server, monkeypatch):
        # deterministic tools must work even with no LLM configured at all
        monkeypatch.setenv("PATH", "")
        result = await _call(server, "check_code", {
            "language": "typescript", "code": "var x = 1;\n"})
        assert any(v["rule_id"] == "ts-no-var" for v in result["violations"])

    async def test_search_by_metadata(self, server):
        hits = await _call(server, "search_conventions", {
            "language": "python", "category": "logging"})
        assert [h["id"] for h in hits] == ["py-no-print"]

    async def test_search_with_code_ranks_by_ast(self, server):
        hits = await _call(server, "search_conventions", {
            "language": "python",
            "code": "@router.get('/x')\nasync def h(s=Depends(d)) -> O:\n    return await s.get()\n",
            "kind": "pattern",
        })
        assert hits[0]["id"] == "py-pattern-fastapi-route"
        assert "ast_similarity" in hits[0]["match_reason"]

    async def test_add_convention_self_verifies(self, server):
        result = await _call(server, "add_convention", {
            "id": "py-no-eval", "language": "python", "title": "no eval",
            "kind": "rule", "rule_type": "forbid",
            "query": '(call function: (identifier) @fn (#eq? @fn "eval"))',
            "message": "eval is forbidden",
            "bad_example": "eval('1+1')\n",
            "good_example": "x = 1 + 1\n",
        })
        assert result["added"] == "py-no-eval"
        check = await _call(server, "check_code", {
            "language": "python", "code": "eval('2')\n"})
        assert any(v["rule_id"] == "py-no-eval" for v in check["violations"])

    async def test_add_broken_convention_rejected(self, server):
        with pytest.raises(Exception, match="does not flag"):
            await _call(server, "add_convention", {
                "id": "broken", "language": "python", "title": "broken",
                "kind": "rule", "rule_type": "forbid",
                "query": '(call function: (identifier) @fn (#eq? @fn "eval"))',
                "message": "x",
                "bad_example": "x = 1\n",  # rule cannot flag this
            })

    async def test_index_codebase(self, server, tmp_path):
        (tmp_path / "m.py").write_text(
            "def handler(event):\n    \"\"\"h\"\"\"\n    return event\n",
            encoding="utf-8")
        result = await _call(server, "index_codebase", {"path": str(tmp_path)})
        assert result["indexed_count"] == 1

    async def test_server_info(self, server):
        info = await _call(server, "server_info", {})
        assert "python" in info["languages"]
        assert info["conventions"] >= 9
