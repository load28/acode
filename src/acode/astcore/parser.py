"""tree-sitter language registry and parsing.

Grammars come from per-language PyPI wheels (no runtime downloads), so
parsing is fully offline and deterministic.
"""

from __future__ import annotations

import importlib
from functools import lru_cache

from tree_sitter import Language, Parser, Tree

# language name -> (module, callable attribute)
_GRAMMARS: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    # vue is a pseudo-language: a .vue SFC's <script> block, parsed with the
    # typescript grammar. parse() blanks out non-script lines so positions
    # in the tree match the original file.
    "vue": ("tree_sitter_typescript", "language_typescript"),
    "go": ("tree_sitter_go", "language"),
    "java": ("tree_sitter_java", "language"),
    "rust": ("tree_sitter_rust", "language"),
}

_ALIASES = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "jsx": "javascript",
    "node": "javascript",
    "ts": "typescript",
    "golang": "go",
    "rs": "rust",
}

_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".tsx": "tsx",
    ".vue": "vue",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
}


class UnsupportedLanguageError(ValueError):
    pass


def normalize_language(name: str) -> str:
    key = name.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in _GRAMMARS:
        raise UnsupportedLanguageError(
            f"unsupported language {name!r}; supported: {', '.join(sorted(_GRAMMARS))}"
        )
    return key


def language_for_path(path: str) -> str | None:
    from pathlib import Path

    lang = _EXTENSIONS.get(Path(path).suffix.lower())
    if lang is None:
        return None
    try:
        get_language(lang)
    except UnsupportedLanguageError:
        return None
    return lang


def supported_languages() -> list[str]:
    """Languages whose grammar wheel is actually importable."""
    available = []
    for name, (module, _) in _GRAMMARS.items():
        try:
            importlib.import_module(module)
            available.append(name)
        except ImportError:
            continue
    return sorted(available)


@lru_cache(maxsize=None)
def get_language(name: str) -> Language:
    key = normalize_language(name)
    module_name, attr = _GRAMMARS[key]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise UnsupportedLanguageError(
            f"grammar package {module_name!r} is not installed "
            f"(pip install {module_name.replace('_', '-')})"
        ) from exc
    return Language(getattr(module, attr)())


def parse(code: str | bytes, language: str) -> Tree:
    text = code.decode("utf-8") if isinstance(code, bytes) else code
    if normalize_language(language) == "vue":
        from .vue import script_only_view

        text = script_only_view(text)
    parser = Parser(get_language(language))
    return parser.parse(text.encode("utf-8"))


def has_syntax_error(tree: Tree) -> bool:
    return tree.root_node.has_error
