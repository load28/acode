"""Index a codebase into pattern conventions.

Walks source files, extracts top-level definitions (functions, classes,
methods...) as snippets, and stores each as a ``pattern`` convention with
its AST fingerprint. Later retrievals can then rank the user's own code
shapes highest, so generated code converges on the project's real style.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..astcore.parser import language_for_path, parse
from .store import Convention, ConventionStore

# node types worth extracting as reusable patterns, per language
_UNIT_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "javascript": {"function_declaration", "class_declaration", "lexical_declaration",
                   "export_statement"},
    "typescript": {"function_declaration", "class_declaration", "interface_declaration",
                   "type_alias_declaration", "lexical_declaration", "export_statement"},
    "tsx": {"function_declaration", "class_declaration", "interface_declaration",
            "lexical_declaration", "export_statement"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "java": {"class_declaration", "interface_declaration", "method_declaration"},
    "rust": {"function_item", "struct_item", "impl_item", "trait_item", "enum_item"},
}

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
              "build", "target", ".next", ".tox", "vendor"}

MIN_SNIPPET_LINES = 3
MAX_SNIPPET_BYTES = 4000


def index_codebase(
    store: ConventionStore,
    root: str | Path,
    language: str | None = None,
    metadata: dict[str, Any] | None = None,
    max_files: int = 500,
) -> dict[str, Any]:
    """Returns {'indexed': [...ids], 'files': n, 'skipped': n}."""
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(str(root))
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob("*")
        if p.is_file() and not any(part in _SKIP_DIRS for part in p.parts)
    )

    indexed: list[str] = []
    seen_files = 0
    skipped = 0
    for path in files:
        lang = language_for_path(str(path))
        if lang is None or (language and lang != language):
            continue
        if seen_files >= max_files:
            skipped += 1
            continue
        seen_files += 1
        try:
            code = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped += 1
            continue
        for snippet, name in _extract_units(code, lang):
            digest = hashlib.md5(snippet.encode("utf-8")).hexdigest()[:10]
            conv_id = f"pattern:{lang}:{name}:{digest}"
            if store.get(conv_id) is not None:
                continue
            conv = Convention(
                id=conv_id,
                kind="pattern",
                language=lang,
                title=f"codebase pattern: {name}",
                guideline=f"Existing pattern from {path.relative_to(root) if root.is_dir() else path.name}. "
                          "Prefer matching this shape when writing similar code.",
                metadata={**(metadata or {}), "source": str(path), "unit": name},
                good_example=snippet,
            )
            store.add(conv, replace=True)
            indexed.append(conv_id)
    return {"indexed": indexed, "files": seen_files, "skipped": skipped}


def _extract_units(code: str, language: str):
    unit_types = _UNIT_TYPES.get(language, set())
    tree = parse(code, language)
    data = code.encode("utf-8")
    for node in tree.root_node.children:
        target = node
        # unwrap decorators/exports to inspect the inner definition type
        inner = node
        if node.type in ("decorated_definition", "export_statement") and node.children:
            named = [c for c in node.children if c.is_named]
            if named:
                inner = named[-1]
        if node.type not in unit_types and inner.type not in unit_types:
            continue
        snippet = data[target.start_byte:target.end_byte].decode("utf-8", errors="replace")
        if len(snippet.encode("utf-8")) > MAX_SNIPPET_BYTES:
            continue
        if snippet.count("\n") + 1 < MIN_SNIPPET_LINES:
            continue
        name = _unit_name(inner) or inner.type
        yield snippet, name


def _unit_name(node) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is not None and name_node.text:
        return name_node.text.decode("utf-8", errors="replace")
    return None
