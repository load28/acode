"""Deterministic AST rule engine.

Rules are expressed as tree-sitter queries and evaluated mechanically —
no LLM is involved at any point, so for a given (code, rule) pair the
result is always identical. The LLM only ever *consumes* the output of
this engine; it never decides whether a rule passed.

Rule types:
    forbid      every query match is a violation
    require     the query must match at least once in the file
    require_in  for every match of ``scope_query``, ``query`` must match
                inside that scope node (e.g. "every function must have a
                docstring")
    naming      text of the ``capture`` in each match must fullmatch
                ``regex``
    semantic    a registered cross-file analyzer (``check`` names it,
                ``params`` tunes thresholds) runs over a whole-project
                model — e.g. React prop-drilling depth with data-origin
                classification. Still fully deterministic; see react.py.

tree-sitter query predicates (#eq?, #match?, #any-of?, ...) are
supported natively inside queries.

Single-string entry points still work for semantic rules: the string may
carry several virtual files separated by ``// @file: path`` marker lines.
``RuleEngine.check_project`` accepts a real ``{path: code}`` mapping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from tree_sitter import Node, Query, QueryCursor, QueryError

from .parser import get_language, parse

RULE_TYPES = ("forbid", "require", "require_in", "naming", "semantic")


class RuleError(ValueError):
    """Raised when a rule definition is invalid (bad query, bad regex...)."""


@dataclass
class Rule:
    id: str
    language: str
    type: str
    query: str = ""
    message: str = ""
    capture: str | None = None
    regex: str | None = None
    scope_query: str | None = None
    severity: str = "error"
    check: str | None = None  # semantic: registered analyzer name
    params: dict[str, Any] = field(default_factory=dict)  # semantic thresholds

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "language": self.language,
            "type": self.type,
            "query": self.query,
            "message": self.message,
            "capture": self.capture,
            "regex": self.regex,
            "scope_query": self.scope_query,
            "severity": self.severity,
            "check": self.check,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        return cls(
            id=data["id"],
            language=data["language"],
            type=data["type"],
            query=data.get("query", ""),
            message=data.get("message", ""),
            capture=data.get("capture"),
            regex=data.get("regex"),
            scope_query=data.get("scope_query"),
            severity=data.get("severity", "error"),
            check=data.get("check"),
            params=data.get("params") or {},
        )


@dataclass
class RuleViolation:
    rule_id: str
    message: str
    severity: str
    start_line: int  # 1-based
    start_col: int
    end_line: int
    end_col: int
    snippet: str = ""
    file: str = ""  # set for project-level (semantic / multi-file) checks

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "severity": self.severity,
            "start_line": self.start_line,
            "start_col": self.start_col,
            "end_line": self.end_line,
            "end_col": self.end_col,
            "snippet": self.snippet,
            "file": self.file,
        }


@dataclass
class CheckReport:
    language: str
    syntax_ok: bool
    checked_rules: list[str] = field(default_factory=list)
    violations: list[RuleViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.syntax_ok and not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "syntax_ok": self.syntax_ok,
            "passed": self.passed,
            "checked_rules": self.checked_rules,
            "violations": [v.to_dict() for v in self.violations],
        }


@lru_cache(maxsize=512)
def _compile(language: str, query: str) -> Query:
    try:
        return Query(get_language(language), query)
    except QueryError as exc:
        raise RuleError(f"invalid tree-sitter query: {exc}") from exc


# tsx is a superset grammar: structural typescript rules also apply to .tsx
# code (their queries are recompiled under the tsx grammar; the rare query
# that doesn't compile there is skipped for that file, not failed). The vue
# pseudo-language parses the SFC's <script> block with the typescript
# grammar, so the same transfer applies. naming rules do NOT transfer —
# naming idioms differ across the boundary (React components are PascalCase
# functions, which a plain-typescript camelCase rule would flag).
_COMPATIBLE_RULE_LANGUAGES = {
    "tsx": ("tsx", "typescript"),
    "vue": ("vue", "typescript"),
}


def _rule_applies(rule: "Rule", code_language: str) -> bool:
    if rule.language == code_language:
        return True
    if rule.type == "naming":
        return False
    return rule.language in _COMPATIBLE_RULE_LANGUAGES.get(code_language, ())


def compatible_rule_languages(code_language: str) -> tuple[str, ...]:
    """Rule languages whose (structural) rules apply to `code_language`."""
    return _COMPATIBLE_RULE_LANGUAGES.get(code_language, (code_language,))


def _semantic_languages() -> tuple[str, ...]:
    """Languages semantic rules exist for. Importing the framework
    front-ends also populates the shared check registry."""
    from .react import REACT_LANGUAGES
    from .vue import VUE_LANGUAGES

    return REACT_LANGUAGES + VUE_LANGUAGES


def _semantic_analyze_source(code: str, language: str):
    """Dispatch a single-string analysis to the right framework front-end."""
    if language == "vue":
        from .vue import analyze_source
    else:
        from .react import analyze_source
    return analyze_source(code, language)


def _semantic_analyze_project(files: dict[str, str], language: str):
    """Dispatch a whole-project analysis, giving each front-end only the
    files it can read (the tsx grammar cannot parse an SFC and vice versa)."""
    from .parser import language_for_path

    if language == "vue":
        from .vue import analyze_project

        subset = {p: c for p, c in files.items()
                  if p.endswith(".vue")
                  or language_for_path(p) in ("typescript", "javascript")}
    else:
        from .react import analyze_project

        subset = {p: c for p, c in files.items() if not p.endswith(".vue")}
    return analyze_project(subset, language)


def validate_rule(rule: Rule) -> None:
    """Raise RuleError if the rule cannot be executed deterministically."""
    if rule.type not in RULE_TYPES:
        raise RuleError(f"unknown rule type {rule.type!r}; expected one of {RULE_TYPES}")
    if rule.type == "semantic":
        supported = _semantic_languages()
        if rule.language not in supported:
            raise RuleError(
                f"semantic rules support {supported}, got {rule.language!r}")
        from .flow import semantic_check_names

        if not rule.check or rule.check not in semantic_check_names():
            raise RuleError(
                f"semantic rule {rule.id!r} needs check= one of: "
                + ", ".join(semantic_check_names()))
        if not isinstance(rule.params, dict):
            raise RuleError(f"semantic rule {rule.id!r}: params must be an object")
        return
    _compile(rule.language, rule.query)
    if rule.type == "require_in":
        if not rule.scope_query:
            raise RuleError("require_in rules need a scope_query")
        _compile(rule.language, rule.scope_query)
    if rule.type == "naming":
        if not rule.capture:
            raise RuleError("naming rules need a capture name")
        if not rule.regex:
            raise RuleError("naming rules need a regex")
        try:
            re.compile(rule.regex)
        except re.error as exc:
            raise RuleError(f"invalid regex: {exc}") from exc


def _matches(rule_query: Query, node: Node) -> list[dict[str, list[Node]]]:
    cursor = QueryCursor(rule_query)
    return [captures for _, captures in cursor.matches(node)]


def _first_node(captures: dict[str, list[Node]], preferred: str | None) -> Node | None:
    if preferred and captures.get(preferred):
        return captures[preferred][0]
    for nodes in captures.values():
        if nodes:
            return nodes[0]
    return None


def _violation(rule: Rule, node: Node | None, message: str | None = None) -> RuleViolation:
    if node is None:
        return RuleViolation(
            rule_id=rule.id,
            message=message or rule.message,
            severity=rule.severity,
            start_line=1,
            start_col=0,
            end_line=1,
            end_col=0,
        )
    return RuleViolation(
        rule_id=rule.id,
        message=message or rule.message,
        severity=rule.severity,
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1],
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1],
        snippet=(node.text or b"").decode("utf-8", errors="replace")[:200],
    )


class RuleEngine:
    """Executes rules against source code. Pure, deterministic, LLM-free."""

    def check(self, code: str, language: str, rules: list[Rule]) -> CheckReport:
        tree = parse(code, language)
        report = CheckReport(language=language, syntax_ok=not tree.root_node.has_error)
        root = tree.root_node
        analysis = None  # built lazily, shared by all semantic rules
        for rule in rules:
            if rule.type == "semantic":
                if rule.language != language:
                    continue
                validate_rule(rule)
                if analysis is None:
                    analysis = _semantic_analyze_source(code, language)
                report.checked_rules.append(rule.id)
                report.violations.extend(self._check_semantic(rule, analysis))
                continue
            if not _rule_applies(rule, language):
                continue
            validate_rule(rule)
            try:
                violations = self._check_rule(rule, root, language)
            except RuleError:
                continue  # query doesn't compile under this grammar variant
            report.checked_rules.append(rule.id)
            report.violations.extend(violations)
        # deterministic ordering: by position, then rule id
        report.violations.sort(
            key=lambda v: (v.file, v.start_line, v.start_col, v.rule_id)
        )
        return report

    def check_project(self, files: dict[str, str], language: str,
                      rules: list[Rule]) -> CheckReport:
        """Check a whole project: semantic rules see all files at once
        (each language's rules run on that language's analysis — a mixed
        React+Vue tree gets both); query rules run per file whose language
        matches."""
        from .parser import language_for_path

        report = CheckReport(language=language, syntax_ok=True)
        semantic = [r for r in rules if r.type == "semantic"]
        single = [r for r in rules if r.type != "semantic"]

        detected = {language} | {
            lang for p in files if (lang := language_for_path(p))}
        analyses: dict[str, Any] = {}
        for rule in semantic:
            if rule.language not in detected:
                continue
            validate_rule(rule)
            if rule.language not in analyses:
                analyses[rule.language] = _semantic_analyze_project(
                    files, rule.language)
                report.syntax_ok = (report.syntax_ok
                                    and analyses[rule.language].syntax_ok)
            report.checked_rules.append(rule.id)
            report.violations.extend(
                self._check_semantic(rule, analyses[rule.language]))

        for path in sorted(files):
            file_language = language_for_path(path) or language
            applicable = [r for r in single
                          if _rule_applies(r, file_language)]
            if not applicable:
                continue
            file_report = self.check(files[path], file_language, applicable)
            report.syntax_ok = report.syntax_ok and file_report.syntax_ok
            for rule_id in file_report.checked_rules:
                if rule_id not in report.checked_rules:
                    report.checked_rules.append(rule_id)
            for violation in file_report.violations:
                violation.file = path
                report.violations.append(violation)

        report.violations.sort(
            key=lambda v: (v.file, v.start_line, v.start_col, v.rule_id)
        )
        return report

    def _check_semantic(self, rule: Rule, analysis: Any) -> list[RuleViolation]:
        from .flow import run_semantic_check

        violations = []
        for finding in run_semantic_check(rule.check or "", analysis, rule.params):
            violations.append(RuleViolation(
                rule_id=rule.id,
                message=f"{rule.message} — {finding.detail}",
                severity=rule.severity,
                start_line=finding.line,
                start_col=finding.col,
                end_line=finding.line,
                end_col=finding.col,
                snippet=finding.snippet[:200],
                file=finding.file,
            ))
        return violations

    def _check_rule(self, rule: Rule, root: Node,
                    language: str | None = None) -> list[RuleViolation]:
        # compile against the grammar the tree was parsed with, which may
        # be a compatible superset of the rule's language (tsx ⊇ typescript)
        query = _compile(language or rule.language, rule.query)
        if rule.type == "forbid":
            return [
                _violation(rule, _first_node(captures, rule.capture))
                for captures in _matches(query, root)
            ]
        if rule.type == "require":
            if not _matches(query, root):
                return [_violation(rule, None)]
            return []
        if rule.type == "require_in":
            scope_query = _compile(language or rule.language, rule.scope_query or "")
            violations = []
            for captures in _matches(scope_query, root):
                scope_node = _first_node(captures, rule.capture)
                if scope_node is None:
                    continue
                if not _matches(query, scope_node):
                    violations.append(_violation(rule, scope_node))
            return violations
        if rule.type == "naming":
            pattern = re.compile(rule.regex or "")
            violations = []
            for captures in _matches(query, root):
                for node in captures.get(rule.capture or "", []):
                    text = (node.text or b"").decode("utf-8", errors="replace")
                    if not pattern.fullmatch(text):
                        violations.append(
                            _violation(rule, node, f"{rule.message} (got {text!r})")
                        )
            return violations
        raise RuleError(f"unknown rule type {rule.type!r}")
