"""Evidence-based rule recommendation over a codebase.

Scans a source tree and judges every stored ``rule`` convention against
it, across the whole rule-complexity spectrum — from single-query rules
(forbid, naming) to multi-signal ``analysis`` rules. Evidence is gathered
per rule type:

    countable sites   naming (captured identifiers), require_in (scopes),
                      require (files), analysis (the analyzer's candidate
                      population, e.g. interfaces with >= 2 optional
                      properties) -> a true conformance ratio
    file dispersion   forbid, and analysis rules without a candidate
                      counter — you cannot count the times a forbidden
                      construct was *not* used, so verdicts fall back to
                      how widely violations spread across files

and folded into a four-way verdict:

    adopt                   dominant practice, lock it in
    fix_first               minority violations, clean up then adopt
    conflicts               the codebase does the opposite; adopting the
                            rule would fight it
    insufficient_evidence   too few governed sites to judge

No LLM anywhere: for a given (codebase, store) pair the report is
byte-for-byte reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tree_sitter import Node

from ..astcore.analyzers import ANALYZER_SITES, get_analyzer
from ..astcore.parser import (
    language_for_path,
    parse,
    resolve_dialect,
    rule_languages,
)
from ..astcore.rules import Rule, RuleError, _compile, _matches
from ..agent.steps import _drop_overridden
from .store import Convention, ConventionStore

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
               "build", "target", ".next", ".tox", "vendor"}

# verdict thresholds (documented in TASK-0010/0011)
ADOPT_CONFORMANCE = 0.9    # sites conforming ratio for straight adoption
FIX_FIRST_CONFORMANCE = 0.5
FIX_FIRST_FILE_RATIO = 0.2  # dispersion-judged rules: violating-file cap
MIN_SITES = 5              # countable evidence needed for a verdict
MIN_FILES = 3              # dispersion-judged evidence needed for a verdict
SATURATION_SITES = 20      # evidence count at which confidence stops growing
MAX_LISTED = 5             # violating files / counter-examples listed


@dataclass
class _RuleStats:
    """Aggregated evidence for one rule across all scanned files."""

    checked_files: int = 0
    sites: int | None = None      # governed sites; None = not countable
    violations: int = 0
    violating_files: set[str] = field(default_factory=set)
    counter_examples: list[str] = field(default_factory=list)


def _count_sites(rule: Rule, root: Node, language: str) -> tuple[int | None, int, list[str]]:
    """(governed sites or None, violations, counter-example texts) for one file.

    Site semantics follow ``astcore.rules.governed_sites`` — naming counts
    captured identifiers, require_in counts scopes, require counts the file,
    analysis counts the analyzer's candidate population. forbid stays
    uncountable here on purpose: for an *adoption* verdict the population
    of "places the construct was avoided" does not exist, so forbid rules
    are judged by violation dispersion instead.
    """
    if rule.type == "analysis":
        found = get_analyzer(rule.analyzer or "")(root, rule, language)
        counter = ANALYZER_SITES.get(rule.analyzer or "")
        sites = counter(root) if counter else None
        return sites, len(found), [v.snippet or v.message for v in found]

    query = _compile(language, rule.query)
    if rule.type == "naming":
        pattern = re.compile(rule.regex or "")
        sites = 0
        bad: list[str] = []
        for captures in _matches(query, root):
            for node in captures.get(rule.capture or "", []):
                sites += 1
                text = (node.text or b"").decode("utf-8", errors="replace")
                if not pattern.fullmatch(text):
                    bad.append(text)
        return sites, len(bad), bad
    if rule.type == "require_in":
        scope_query = _compile(language, rule.scope_query or "")
        sites = 0
        bad = []
        for captures in _matches(scope_query, root):
            nodes = captures.get(rule.capture or "") or next(
                (n for n in captures.values() if n), [])
            if not nodes:
                continue
            sites += 1
            if not _matches(query, nodes[0]):
                text = (nodes[0].text or b"").decode("utf-8", errors="replace")
                bad.append(text.splitlines()[0][:80] if text else rule.message)
        return sites, len(bad), bad
    if rule.type == "require":
        matched = bool(_matches(query, root))
        return 1, 0 if matched else 1, [] if matched else [rule.message]
    if rule.type == "forbid":
        found = _matches(query, root)
        snippets = []
        for captures in found:
            node = next((n[0] for n in captures.values() if n), None)
            if node is not None:
                text = (node.text or b"").decode("utf-8", errors="replace")
                snippets.append(text.splitlines()[0][:80])
        return None, len(found), snippets
    raise RuleError(f"unknown rule type {rule.type!r}")


def _verdict(stats: _RuleStats, min_sites: int) -> tuple[str, float, str]:
    """(verdict, confidence, reason). Deterministic in the stats.

    confidence = base * saturation — base is the conformance ratio (or
    1 - violating-file dispersion when sites are not countable), saturation
    ramps with evidence volume so 3 perfect sites cannot outrank 40
    near-perfect ones.
    """
    if stats.sites is not None:
        if stats.sites < min_sites:
            return ("insufficient_evidence", 0.0,
                    f"only {stats.sites} governed site(s); need {min_sites}")
        conformance = 1 - stats.violations / stats.sites
        saturation = min(1.0, stats.sites / SATURATION_SITES)
        confidence = round(conformance * saturation, 4)
        detail = (f"{stats.sites - stats.violations}/{stats.sites} sites conform "
                  f"({conformance:.0%})")
        if conformance >= ADOPT_CONFORMANCE:
            return "adopt", confidence, detail + " — dominant practice"
        if conformance >= FIX_FIRST_CONFORMANCE:
            return ("fix_first", confidence,
                    detail + " — clean up the minority violations, then adopt")
        return ("conflicts", confidence,
                detail + " — the codebase leans the other way")
    # not countable: judge by how widely violations are dispersed
    if stats.checked_files < MIN_FILES:
        return ("insufficient_evidence", 0.0,
                f"only {stats.checked_files} file(s) checked; need {MIN_FILES}")
    ratio = len(stats.violating_files) / stats.checked_files
    saturation = min(1.0, stats.checked_files / SATURATION_SITES)
    confidence = round((1 - ratio) * saturation, 4)
    if stats.violations == 0:
        return ("adopt", confidence,
                f"0 violations across {stats.checked_files} files — no counter-evidence")
    detail = (f"{stats.violations} violation(s) in {len(stats.violating_files)}"
              f"/{stats.checked_files} files")
    if ratio <= FIX_FIRST_FILE_RATIO:
        return "fix_first", confidence, detail + " — contained; fix then adopt"
    return "conflicts", confidence, detail + " — widespread current practice"


def _source_files(root: Path, language: str | None, max_files: int
                  ) -> tuple[list[tuple[Path, str]], int]:
    """Sorted (path, language) pairs plus the count of files skipped."""
    candidates = [root] if root.is_file() else sorted(
        p for p in root.rglob("*")
        if p.is_file() and not any(part in _SKIP_DIRS for part in p.parts)
    )
    picked: list[tuple[Path, str]] = []
    skipped = 0
    for path in candidates:
        lang = language_for_path(str(path))
        if lang is None or (language and lang != language):
            continue
        if len(picked) >= max_files:
            skipped += 1
            continue
        picked.append((path, lang))
    return picked, skipped


def recommend_rules(
    store: ConventionStore,
    root: str | Path,
    language: str | None = None,
    max_files: int = 500,
    min_sites: int = MIN_SITES,
) -> dict[str, Any]:
    """Scan ``root`` and judge every stored rule. Deterministic, LLM-free."""
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(str(root))
    files, skipped = _source_files(root, language, max_files)

    lang_counts: dict[str, int] = {}
    stats: dict[str, _RuleStats] = {}
    rules_by_id: dict[str, tuple[Convention, Rule]] = {}
    rules_cache: dict[str, list[tuple[Convention, Rule]]] = {}

    for path, file_lang in files:
        try:
            code = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped += 1
            continue
        file_lang = resolve_dialect(code, file_lang)
        lang_counts[file_lang] = lang_counts.get(file_lang, 0) + 1
        tree = parse(code, file_lang)
        if tree.root_node.has_error:
            skipped += 1
            continue
        rel = str(path.relative_to(root)) if root.is_dir() else path.name

        if file_lang not in rules_cache:
            conventions = [
                conv for conv in store.list(kind="rule") if conv.rule is not None
            ]
            applicable = [
                conv for conv in conventions
                if conv.language in rule_languages(file_lang)
            ]
            rules_cache[file_lang] = [
                (conv, conv.rule)
                for conv in _drop_overridden(applicable, file_lang)
            ]
        for conv, rule in rules_cache[file_lang]:
            rules_by_id[conv.id] = (conv, rule)
            entry = stats.setdefault(conv.id, _RuleStats())
            try:
                sites, violations, examples = _count_sites(
                    rule, tree.root_node, file_lang)
            except RuleError:
                continue  # query targets a node the dialect grammar lacks
            entry.checked_files += 1
            if sites is not None:
                entry.sites = (entry.sites or 0) + sites
            entry.violations += violations
            if violations:
                entry.violating_files.add(rel)
                entry.counter_examples.extend(examples)

    catalog: list[dict[str, Any]] = []
    for conv_id in sorted(stats):
        conv, rule = rules_by_id[conv_id]
        entry = stats[conv_id]
        verdict, confidence, reason = _verdict(entry, min_sites)
        catalog.append({
            "id": conv.id,
            "title": conv.title,
            "language": conv.language,
            "rule_type": rule.type,
            "verdict": verdict,
            "confidence": confidence,
            "reason": reason,
            "evidence": {
                "checked_files": entry.checked_files,
                "sites": entry.sites,
                "violations": entry.violations,
                "violating_files": sorted(entry.violating_files)[:MAX_LISTED],
                "counter_examples": entry.counter_examples[:MAX_LISTED],
            },
        })
    _VERDICT_ORDER = {"adopt": 0, "fix_first": 1, "conflicts": 2,
                      "insufficient_evidence": 3}
    catalog.sort(key=lambda r: (_VERDICT_ORDER[r["verdict"]],
                                -r["confidence"], r["id"]))

    return {
        "root": str(root),
        "files": len(files),
        "skipped": skipped,
        "languages": dict(sorted(lang_counts.items())),
        "catalog": catalog,
    }
