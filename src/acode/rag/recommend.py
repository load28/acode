"""Evidence-based rule recommendation over a codebase.

Scans a source tree and produces two deterministic recommendation lists:

    catalog     every stored ``rule`` convention judged against the code.
                Evidence is gathered per rule type — governed sites and a
                conformance ratio where sites are countable (naming,
                require_in, require), file dispersion where they are not
                (forbid, analysis) — and folded into a four-way verdict:
                    adopt                   dominant practice, lock it in
                    fix_first               minority violations, clean up
                                            then adopt
                    conflicts               the codebase does the opposite;
                                            adopting the rule would fight it
                    insufficient_evidence   too few governed sites to judge
    proposals   naming conventions mined from the codebase that the catalog
                does not cover yet. A style is proposed only when it is
                dominant AND has the most *exclusive* evidence (samples that
                match no competing style), so ambiguous identifiers like
                ``fetch`` — valid camelCase and snake_case at once — never
                manufacture a convention. Each proposal is self-verified
                with the rule engine before being returned, so it can be
                inserted via ``ConventionStore.add`` as-is.

No LLM anywhere: for a given (codebase, store) pair the report is
byte-for-byte reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tree_sitter import Node

from ..astcore.analyzers import get_analyzer
from ..astcore.parser import (
    language_for_path,
    parse,
    resolve_dialect,
    rule_languages,
)
from ..astcore.rules import Rule, RuleError, RuleEngine, _compile, _matches, validate_rule
from ..agent.steps import _drop_overridden
from .store import Convention, ConventionStore

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
               "build", "target", ".next", ".tox", "vendor"}

# verdict thresholds (documented in TASK-0010)
ADOPT_CONFORMANCE = 0.9    # sites conforming ratio for straight adoption
FIX_FIRST_CONFORMANCE = 0.5
FIX_FIRST_FILE_RATIO = 0.2  # forbid/analysis: violating-file dispersion cap
MIN_SITES = 5              # countable evidence needed for a verdict
MIN_FILES = 3              # forbid/analysis evidence needed for a verdict
SATURATION_SITES = 20      # evidence count at which confidence stops growing
MAX_LISTED = 5             # violating files / counter-examples listed


# --------------------------------------------------------------- evidence


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

    Site semantics per rule type:
        naming      every captured identifier is a site
        require_in  every scope match is a site
        require     the file itself is the single site
        forbid      sites are not countable — you cannot count the times a
                    construct was *not* used; only violations are observable
        analysis    same: analyzers report violations, not opportunities
    """
    if rule.type == "analysis":
        found = get_analyzer(rule.analyzer or "")(root, rule, language)
        return None, len(found), [v.snippet or v.message for v in found]

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
    # forbid/analysis: judge by how widely violations are dispersed
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


# ----------------------------------------------------------------- mining


@dataclass(frozen=True)
class _MiningTarget:
    construct: str          # human name: "function", "class", ...
    query: str              # tree-sitter query with a @name capture
    example: str            # declaration template, {name} placeholder


_STYLES: tuple[tuple[str, str], ...] = (
    ("camelCase", r"[a-z][a-zA-Z0-9]*"),
    ("PascalCase", r"[A-Z][a-zA-Z0-9]*"),
    ("snake_case", r"_{0,2}[a-z][a-z0-9_]*_{0,2}"),
    ("SCREAMING_SNAKE_CASE", r"[A-Z][A-Z0-9_]*"),
)

# names guaranteed to violate at least one style each; the first one that
# fails the winning regex becomes the proposal's bad_example
_BAD_NAME_CANDIDATES = ("Bad_Name", "badName", "BadName", "bad_name")

# go/java/rust are deliberately absent: their node names are unverified here
# and a silently wrong query would mine nothing (or nonsense). Catalog
# verdicts still cover them; extend after grammar checks (TASK-0010 handoff).
_MINING_TARGETS: dict[str, tuple[_MiningTarget, ...]] = {
    "python": (
        _MiningTarget("function", "(function_definition name: (identifier) @name)",
                      "def {name}():\n    pass\n"),
        _MiningTarget("class", "(class_definition name: (identifier) @name)",
                      "class {name}:\n    pass\n"),
    ),
    "javascript": (
        _MiningTarget("function", "(function_declaration name: (identifier) @name)",
                      "function {name}() {{}}\n"),
        _MiningTarget("class", "(class_declaration name: (identifier) @name)",
                      "class {name} {{}}\n"),
    ),
    "typescript": (
        _MiningTarget("function", "(function_declaration name: (identifier) @name)",
                      "function {name}() {{}}\n"),
        _MiningTarget("class", "(class_declaration name: (type_identifier) @name)",
                      "class {name} {{}}\n"),
        _MiningTarget("interface", "(interface_declaration name: (type_identifier) @name)",
                      "interface {name} {{ x: number; }}\n"),
        _MiningTarget("type alias", "(type_alias_declaration name: (type_identifier) @name)",
                      "type {name} = string;\n"),
    ),
}


def _normalize_query(query: str) -> str:
    return " ".join(query.split())


def _mine_naming(
    store: ConventionStore,
    names_by_target: dict[tuple[str, _MiningTarget], list[str]],
    min_sites: int,
) -> list[dict[str, Any]]:
    """Turn collected identifiers into self-verified naming-rule proposals."""
    catalog_naming = {
        (conv.language, _normalize_query(conv.rule.query))
        for conv in store.list(kind="rule")
        if conv.rule is not None and conv.rule.type == "naming"
    }
    engine = RuleEngine()
    proposals: list[dict[str, Any]] = []
    for (language, target), names in sorted(
        names_by_target.items(), key=lambda kv: (kv[0][0], kv[0][1].construct)
    ):
        if (language, _normalize_query(target.query)) in catalog_naming:
            continue  # already governed by a stored rule; judged in `catalog`
        if len(names) < min_sites:
            continue

        compiled = [(style, re.compile(regex)) for style, regex in _STYLES]
        matches_per_name = [
            {style for style, pattern in compiled if pattern.fullmatch(name)}
            for name in names
        ]
        scored = []  # (style, regex, conforming, exclusive)
        for style, regex in _STYLES:
            conforming = sum(1 for m in matches_per_name if style in m)
            exclusive = sum(1 for m in matches_per_name if m == {style})
            scored.append((style, regex, conforming, exclusive))
        # dominant = highest conformance; deterministic tie-break on style order
        style, regex, conforming, exclusive = max(
            scored, key=lambda s: (s[2], s[3], -_style_rank(s[0])))
        conformance = conforming / len(names)
        if conformance < ADOPT_CONFORMANCE:
            continue
        # require strictly-leading exclusive evidence: without a sample that
        # matches ONLY this style, the choice between overlapping styles
        # (fetch -> camelCase AND snake_case) would be arbitrary
        rivals = max((s[3] for s in scored if s[0] != style), default=0)
        if exclusive == 0 or exclusive <= rivals:
            continue

        good_name = next(
            (n for n, m in zip(names, matches_per_name) if m == {style}))
        bad_name = next(
            n for n in _BAD_NAME_CANDIDATES if not re.fullmatch(regex, n))
        slug = re.sub(r"[^a-z0-9]+", "-", target.construct.lower()).strip("-")
        rule = Rule(
            id=f"mined-{language}-{slug}-naming",
            language=language,
            type="naming",
            query=target.query,
            message=f"{target.construct} names must be {style}",
            capture="name",
            regex=regex,
        )
        convention = {
            "id": rule.id,
            "kind": "rule",
            "language": language,
            "title": f"{target.construct} names are {style} (mined)",
            "guideline": (
                f"Mined from the codebase: {conforming}/{len(names)} "
                f"{target.construct} names are {style} "
                f"({exclusive} unambiguously so)."),
            "metadata": {"category": "naming", "tags": ["mined"]},
            "rule": rule.to_dict(),
            "good_example": target.example.format(name=good_name),
            "bad_example": target.example.format(name=bad_name),
        }
        # same self-verification the store performs on insert
        validate_rule(rule)
        if engine.check(convention["bad_example"], language, [rule]).violations \
                and not engine.check(convention["good_example"], language, [rule]).violations:
            counter = sorted(
                {n for n, m in zip(names, matches_per_name) if style not in m}
            )[:MAX_LISTED]
            saturation = min(1.0, len(names) / SATURATION_SITES)
            proposals.append({
                "id": rule.id,
                "title": convention["title"],
                "verdict": "propose",
                "confidence": round(conformance * saturation, 4),
                "reason": (
                    f"{conforming}/{len(names)} {target.construct} names are "
                    f"{style}; {exclusive} match no other style"),
                "evidence": {
                    "sites": len(names),
                    "conforming": conforming,
                    "exclusive": exclusive,
                    "counter_examples": counter,
                },
                "convention": convention,
            })
    return proposals


def _style_rank(style: str) -> int:
    return next(i for i, (name, _) in enumerate(_STYLES) if name == style)


# ------------------------------------------------------------------ scan


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
    mine: bool = True,
) -> dict[str, Any]:
    """Scan ``root`` and recommend conventions. Deterministic, LLM-free."""
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(str(root))
    files, skipped = _source_files(root, language, max_files)

    lang_counts: dict[str, int] = {}
    stats: dict[str, _RuleStats] = {}
    rules_by_id: dict[str, tuple[Convention, Rule]] = {}
    names_by_target: dict[tuple[str, _MiningTarget], list[str]] = {}
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

        if mine:
            # dialects mine into their base language (tsx -> typescript)
            mining_lang = rule_languages(file_lang)[-1]
            for target in _MINING_TARGETS.get(mining_lang, ()):
                try:
                    query = _compile(file_lang, target.query)
                except RuleError:
                    continue
                bucket = names_by_target.setdefault((mining_lang, target), [])
                for captures in _matches(query, tree.root_node):
                    for node in captures.get("name", []):
                        bucket.append(
                            (node.text or b"").decode("utf-8", errors="replace"))

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

    proposals = _mine_naming(store, names_by_target, min_sites) if mine else []
    proposals.sort(key=lambda p: (-p["confidence"], p["id"]))

    return {
        "root": str(root),
        "files": len(files),
        "skipped": skipped,
        "languages": dict(sorted(lang_counts.items())),
        "catalog": catalog,
        "proposals": proposals,
    }
