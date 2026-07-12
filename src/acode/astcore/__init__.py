from .parser import get_language, parse, supported_languages, normalize_language
from .fingerprint import fingerprint_code, fingerprint_node, cosine
from .rules import Rule, RuleViolation, RuleEngine, RuleError
from .react import (
    analyze_project,
    analyze_source,
    semantic_check_names,
    split_virtual_files,
)
from . import vue as _vue  # noqa: F401 — registers the vue-* semantic checks

__all__ = [
    "get_language",
    "parse",
    "supported_languages",
    "normalize_language",
    "fingerprint_code",
    "fingerprint_node",
    "cosine",
    "Rule",
    "RuleViolation",
    "RuleEngine",
    "RuleError",
    "analyze_project",
    "analyze_source",
    "semantic_check_names",
    "split_virtual_files",
]
