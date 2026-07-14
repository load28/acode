"""MCP server exposing the convention agent to MCP clients (Claude Code,
or any other MCP-capable agent).

Register with Claude Code:

    claude mcp add acode -- acode serve

Tools:
    search_conventions  deterministic RAG lookup (metadata + AST similarity)
    check_code          deterministic AST rule verdict — no LLM involved
    generate_code       full pipeline: retrieve -> LLM -> verify -> repair
    review_code         mechanical verdict + LLM-synthesized review & fix
    add_convention      store a rule/pattern (rules are self-verified on insert)
    list_conventions    enumerate stored conventions
    delete_convention   remove a convention
    index_codebase      ingest a repo's code shapes as retrieval patterns
    recommend_rules     evidence-based adoption verdicts for every stored
                        rule against a codebase, simple and complex (no LLM)

check_code / search_conventions never call an LLM, so their output is
reproducible byte-for-byte; generate/review responses always embed the
mechanical report so callers can trust `verified` without trusting prose.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..agent.pipeline import CodingPipeline
from ..agent import steps
from ..astcore.parser import supported_languages
from ..astcore.rules import Rule
from ..config import AcodeConfig
from ..rag.indexer import index_codebase as _index_codebase
from ..rag.store import Convention, ConventionStore


def _metadata_from(framework: str | None, category: str | None,
                   tags: list[str] | None,
                   extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = dict(extra or {})
    if framework:
        metadata["framework"] = framework
    if category:
        metadata["category"] = category
    if tags:
        metadata["tags"] = tags
    return metadata


def _make_backend(store: ConventionStore, config: AcodeConfig):
    """Prefer the ADK orchestration; fall back to the plain pipeline."""
    try:
        from ..llm.factory import create_provider
        from ..agent.adk import AdkCodingAgent

        return AdkCodingAgent(store, create_provider(config), config)
    except ImportError:
        return CodingPipeline(store, None, config)


def build_server(config: AcodeConfig | None = None,
                 store: ConventionStore | None = None) -> FastMCP:
    config = config or AcodeConfig()
    store = store or ConventionStore(config.db_path)

    mcp = FastMCP(
        "acode",
        instructions=(
            "AST-grounded coding-convention agent. Conventions are stored as "
            "executable tree-sitter rules and verified mechanically; use "
            "check_code for deterministic verdicts, generate_code/review_code "
            "for LLM answers that are already mechanically verified."
        ),
    )

    @mcp.tool()
    def search_conventions(
        language: str,
        query: str | None = None,
        code: str | None = None,
        framework: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        kind: str | None = None,
        top_k: int = 8,
    ) -> str:
        """Hybrid convention search. Deterministic: hard metadata filter,
        then a weighted blend of BM25 (`query` keywords), AST-fingerprint
        similarity (`code` — pass the code being modified so the closest
        patterns rank first), rule applicability (how many sites in `code`
        each rule structurally governs — this is what surfaces complex
        analysis rules whose preconditions match even before a violation
        exists), and metadata overlap."""
        hits = store.search(
            language=language,
            metadata=_metadata_from(framework, category, tags),
            code=code,
            query=query,
            kind=kind,
            top_k=top_k,
        )
        return json.dumps([h.to_dict() for h in hits], ensure_ascii=False, indent=2)

    @mcp.tool()
    def check_code(
        language: str,
        code: str,
        framework: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        convention_ids: list[str] | None = None,
    ) -> str:
        """Mechanically check code against stored convention rules using the
        AST engine. No LLM is involved: the verdict is deterministic and
        reproducible. Returns syntax status and rule violations with
        line/column positions."""
        from ..astcore.parser import resolve_dialect

        # JSX code declared as "typescript" is checked as tsx; tsx inherits
        # the typescript ruleset (dialect rules may override base rules)
        language = resolve_dialect(code, language)
        if convention_ids:
            rules = []
            for cid in convention_ids:
                conv = store.get(cid)
                if conv and conv.rule:
                    rules.append(conv.rule)
        else:
            rules = steps.applicable_rules(
                store, language, _metadata_from(framework, category, tags))
        report = steps.check(code, language, rules)
        return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)

    @mcp.tool()
    async def generate_code(
        language: str,
        task: str,
        framework: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        context_code: str | None = None,
    ) -> str:
        """Generate code that follows stored conventions. Pipeline:
        deterministic RAG retrieval -> LLM synthesis -> mechanical AST
        verification -> bounded LLM repair loop. The response includes the
        mechanical report; trust `verified`, not prose."""
        backend = _make_backend(store, config)
        result = await backend.generate(
            task=task,
            language=language,
            metadata=_metadata_from(framework, category, tags),
            context_code=context_code,
        )
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)

    @mcp.tool()
    async def review_code(
        language: str,
        code: str,
        instruction: str | None = None,
        framework: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Review/modify existing code. Retrieval uses metadata + AST
        similarity of the given code; the mechanical rule verdict is computed
        first and handed to the LLM as ground truth, and the LLM's suggested
        fix is mechanically re-verified before being returned."""
        backend = _make_backend(store, config)
        result = await backend.review(
            code=code,
            language=language,
            metadata=_metadata_from(framework, category, tags),
            instruction=instruction,
        )
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)

    @mcp.tool()
    def add_convention(
        id: str,
        language: str,
        title: str,
        kind: str = "rule",
        guideline: str = "",
        rule_type: str | None = None,
        query: str | None = None,
        message: str | None = None,
        capture: str | None = None,
        regex: str | None = None,
        scope_query: str | None = None,
        analyzer: str | None = None,
        severity: str = "error",
        good_example: str = "",
        bad_example: str = "",
        framework: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        replace: bool = False,
    ) -> str:
        """Store a convention. kind='rule' needs rule_type
        (forbid|require|require_in|naming|analysis) and a message, plus a
        tree-sitter query (query types) or a built-in analyzer name
        (type='analysis'); the rule is validated mechanically on insert (it
        must flag bad_example and pass good_example). kind='pattern' needs a
        good_example snippet and is used for AST-similarity retrieval."""
        rule = None
        if kind == "rule":
            if not (rule_type and message):
                raise ValueError("kind='rule' requires rule_type and message")
            if rule_type == "analysis":
                if not analyzer:
                    raise ValueError("rule_type='analysis' requires analyzer")
            elif not query:
                raise ValueError(f"rule_type={rule_type!r} requires query")
            rule = Rule(
                id=id, language=language, type=rule_type, query=query or "",
                message=message, capture=capture, regex=regex,
                scope_query=scope_query, severity=severity, analyzer=analyzer,
            )
        conv = Convention(
            id=id, kind=kind, language=language, title=title,
            guideline=guideline,
            metadata=_metadata_from(framework, category, tags),
            rule=rule, good_example=good_example, bad_example=bad_example,
        )
        store.add(conv, replace=replace)
        return json.dumps({"added": id, "kind": kind, "self_verified": kind == "rule"})

    @mcp.tool()
    def list_conventions(language: str | None = None, kind: str | None = None) -> str:
        """List stored conventions (id, kind, title, metadata)."""
        entries = [
            {"id": c.id, "kind": c.kind, "language": c.language,
             "title": c.title, "metadata": c.metadata}
            for c in store.list(language=language, kind=kind)
        ]
        return json.dumps(entries, ensure_ascii=False, indent=2)

    @mcp.tool()
    def delete_convention(id: str) -> str:
        """Delete a convention by id."""
        return json.dumps({"deleted": store.delete(id), "id": id})

    @mcp.tool()
    def index_codebase(
        path: str,
        language: str | None = None,
        framework: str | None = None,
        tags: list[str] | None = None,
        max_files: int = 500,
    ) -> str:
        """Index a file or directory: extracts top-level definitions as
        `pattern` conventions with AST fingerprints so future generation and
        review rank the project's own code shapes highest."""
        result = _index_codebase(
            store, path, language=language,
            metadata=_metadata_from(framework, None, tags),
            max_files=max_files,
        )
        return json.dumps(
            {"files": result["files"], "skipped": result["skipped"],
             "indexed_count": len(result["indexed"]),
             "indexed": result["indexed"][:50]},
            ensure_ascii=False,
        )

    @mcp.tool()
    def recommend_rules(
        path: str,
        language: str | None = None,
        max_files: int = 500,
        min_sites: int = 5,
    ) -> str:
        """Scan a codebase (or a single file) and judge every stored rule
        against it, deterministically: adopt / fix_first / conflicts /
        insufficient_evidence, with per-rule evidence — governed sites,
        conformance ratio, violating files. Covers the whole rule
        complexity spectrum: analysis rules count their candidate
        populations (e.g. interfaces with >= 2 optional properties), so
        their verdicts are as evidence-based as simple naming rules."""
        from ..rag.recommend import recommend_rules as _recommend

        report = _recommend(
            store, path, language=language, max_files=max_files,
            min_sites=min_sites,
        )
        return json.dumps(report, ensure_ascii=False, indent=2)

    @mcp.tool()
    def server_info() -> str:
        """Supported languages, database location, and LLM configuration."""
        from ..llm.claude_code import ClaudeCodeProvider

        return json.dumps({
            "languages": supported_languages(),
            "db_path": store.db_path,
            "conventions": len(store.list()),
            "search_engines": {
                "lexical": store.lexical_engine().name,
                "vector": store.vector_engine().name,
            },
            "llm_provider_configured": config.llm_provider or "auto",
            "claude_cli_available": ClaudeCodeProvider.available(config.claude_bin),
        }, ensure_ascii=False, indent=2)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
