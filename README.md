# acode

AST-grounded convention coding agent, built on **Google ADK** and served over
**MCP** so any MCP-capable agent (Claude Code, etc.) can use it.

The core idea: coding conventions live in a RAG store **as executable AST
rules**, not prose. Whether code follows a rule is decided **mechanically and
deterministically** by a tree-sitter rule engine — never guessed by an LLM.
The LLM's only job is synthesis: writing code and explaining results, with the
mechanical verdict handed to it as ground truth. Two layers, one stable
answer:

```
                ┌──────────────────────────────────────────────┐
 MCP client     │  acode (ADK agent, MCP server)               │
 (Claude Code)  │                                              │
 ──────────────▶│  1. RAG retrieval        deterministic       │
   generate /   │     metadata filter + AST-fingerprint rank   │
   review /     │  2. LLM synthesis        the only LLM step   │
   check        │  3. AST rule engine      deterministic       │
                │  4. repair loop          bounded, re-verified│
                │                                              │
                │  SQLite ── conventions as tree-sitter rules  │
                └──────────────────────────────────────────────┘
```

## Key properties

- **Rules are executable, not prose.** A convention is a tree-sitter query
  (`forbid` / `require` / `require_in` / `naming`). The same code + rule
  always produces the same verdict, with line/column positions.
- **Rules are self-verified on insert.** A rule convention must mechanically
  flag its own `bad_example` and pass its own `good_example`, or the store
  rejects it. You cannot store a convention that can't be demonstrated.
- **Retrieval is a deterministic hybrid search engine on leading OSS.**
  Text queries run BM25 on [Tantivy](https://github.com/quickwit-oss/tantivy)
  (the Rust successor to Lucene; identifiers split on snake_case/camelCase),
  and AST fingerprints — feature-hashed structural vectors (node-type
  unigrams + parent>child bigrams, identifiers excluded; no embedding
  model) — run exact cosine search on
  [FAISS](https://github.com/facebookresearch/faiss) (`IndexFlatIP`).
  Metadata (language/framework/category/tags) hard-filters. Signals blend as
  0.45·AST + 0.35·BM25 + 0.20·metadata (renormalized over the signals
  present). Both engines are exact, so determinism holds: same corpus + same
  query = same ranking, always. Zero-dependency builtin fallbacks (own BM25 +
  Python cosine) engage automatically when the engines aren't installed;
  pin with `ACODE_LEXICAL_ENGINE` / `ACODE_VECTOR_ENGINE`.
- **The LLM only synthesizes.** Generated code is mechanically verified; on
  violations a bounded repair loop feeds the exact violations back. Review
  responses embed the mechanical report — trust `verified`, not prose.
- **Runs on local Claude Code by default.** If the `claude` CLI is installed,
  no API key is needed. Otherwise any provider/model/API key works via
  environment variables.

## Install

```bash
pip install -e .            # core (tree-sitter, MCP, python/js/ts grammars)
pip install -e '.[search]'  # + Tantivy (BM25) & FAISS (vector) engines (recommended)
pip install -e '.[adk]'     # + Google ADK orchestration (recommended)
pip install -e '.[langs]'   # + go, java, rust grammars
pip install -e '.[litellm]' # + litellm (100+ providers)
```

## Quick start

```bash
# 1. build the corpus: curated conventions + your codebase's own patterns
acode corpus build --index ./src

# 2. inspect what went in / try the search engine
acode corpus stats
acode search --language python --query "logging print forbidden"
acode search --language python --code-file some/route.py   # rank by AST shape

# 3. register the MCP server with Claude Code
claude mcp add acode -- acode serve
```

The convention JSON files in `conventions/` are the corpus's source of truth;
the SQLite database is a build artifact — rerun `acode corpus build` any time
the corpus sources change. (`acode import` / `acode index` also update an
existing database incrementally.)

Then from Claude Code (or any MCP client):

| tool | LLM? | what it does |
|---|---|---|
| `check_code` | no | deterministic AST rule verdict with positions |
| `search_conventions` | no | metadata filter + AST-similarity ranking |
| `list/add/delete_convention` | no | manage rules (self-verified on insert) |
| `index_codebase` | no | ingest code shapes as retrieval patterns |
| `generate_code` | yes | retrieve → synthesize → verify → repair |
| `review_code` | yes | mechanical verdict → LLM review + fix → re-verify |

## LLM configuration

Default: the local **Claude Code CLI** (`claude -p`, single turn, no tools).
Override with environment variables:

```bash
export ACODE_LLM_PROVIDER=anthropic   # claude-code | anthropic | openai | litellm
export ACODE_LLM_MODEL=claude-sonnet-5
export ACODE_LLM_API_KEY=sk-...
export ACODE_LLM_BASE_URL=...         # OpenAI-compatible servers: Ollama, vLLM,
                                      # OpenRouter, LM Studio, Groq, ...
```

`openai` speaks the `/chat/completions` protocol, so any self-hosted or
hosted OpenAI-compatible endpoint works with just these four variables.

## Writing a convention

```json
{
  "id": "py-no-print",
  "kind": "rule",
  "language": "python",
  "title": "Use logging instead of print()",
  "guideline": "Application code must not call print(); use the module logger.",
  "metadata": {"category": "logging", "tags": ["logging"]},
  "rule": {
    "id": "py-no-print",
    "language": "python",
    "type": "forbid",
    "query": "(call function: (identifier) @fn (#eq? @fn \"print\"))",
    "capture": "fn",
    "message": "print() is forbidden; use logger.info/debug instead"
  },
  "good_example": "import logging\nlogging.getLogger(__name__).info('x')\n",
  "bad_example": "print('x')\n"
}
```

Rule types:

- `forbid` — every query match is a violation
- `require` — the query must match somewhere in the file
- `require_in` — for each `scope_query` match, `query` must match inside it
  (e.g. every function has a docstring)
- `naming` — the `capture`'s text must fullmatch `regex`

`kind: "pattern"` entries skip the rule and store a canonical snippet whose
AST fingerprint steers retrieval (this is what `acode index` produces from
your codebase).

## Architecture

- `acode.astcore` — tree-sitter parsing, structural fingerprints, the
  deterministic rule engine (`RuleEngine.check` is a pure function)
- `acode.rag` — SQLite convention store, deterministic search, codebase
  indexer
- `acode.llm` — providers: `claude-code` CLI, Anthropic API,
  OpenAI-compatible HTTP, litellm
- `acode.agent` — pipeline steps + two implementations: plain asyncio
  (`pipeline.py`) and Google ADK agents (`adk.py`: Sequential/Loop agents,
  where the verify agent escalates to end the repair loop; also provides
  `ClaudeCodeLlm`, a `BaseLlm` adapter so any ADK `LlmAgent` can run on the
  local Claude Code CLI)
- `acode.mcpserver` — FastMCP stdio server exposing the tools above

Languages supported out of the box: Python, JavaScript, TypeScript, TSX
(+ Go, Java, Rust with the `langs` extra).

## Development

```bash
pip install -e '.[adk,dev]'
pytest
```

All work on this repo is tracked per task in [`docs/tasks/INDEX.md`](docs/tasks/INDEX.md)
(task docs record per-file changes, decisions, and verification results so any
session can pick up where the last one left off). Workflow rules live in
[`CLAUDE.md`](CLAUDE.md).

## License

MIT
