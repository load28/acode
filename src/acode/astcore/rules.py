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

tree-sitter query predicates (#eq?, #match?, #any-of?, ...) are
supported natively inside queries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from tree_sitter import Node, Query, QueryCursor, QueryError

from .parser import get_language, parse, resolve_dialect, rule_languages

RULE_TYPES = ("forbid", "require", "require_in", "naming", "analysis")


class RuleError(ValueError):
    """Raised when a rule definition is invalid (bad query, bad regex...)."""


@dataclass
class Rule:
    id: str
    language: str
    type: str
    query: str
    message: str
    capture: str | None = None
    regex: str | None = None
    scope_query: str | None = None
    severity: str = "error"
    analyzer: str | None = None  # analysis rules: name in analyzers.ANALYZERS

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
            "analyzer": self.analyzer,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        return cls(
            id=data["id"],
            language=data["language"],
            type=data["type"],
            query=data["query"],
            message=data["message"],
            capture=data.get("capture"),
            regex=data.get("regex"),
            scope_query=data.get("scope_query"),
            severity=data.get("severity", "error"),
            analyzer=data.get("analyzer"),
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
        }


@dataclass
class CheckReport:
    language: str
    syntax_ok: bool
    checked_rules: list[str] = field(default_factory=list)
    violations: list[RuleViolation] = field(default_factory=list)
    # rules whose query does not compile under the dialect grammar actually
    # used for the check — reported instead of silently dropped
    skipped_rules: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.syntax_ok and not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "syntax_ok": self.syntax_ok,
            "passed": self.passed,
            "checked_rules": self.checked_rules,
            "skipped_rules": self.skipped_rules,
            "violations": [v.to_dict() for v in self.violations],
        }


@lru_cache(maxsize=512)
def _compile(language: str, query: str) -> Query:
    try:
        return Query(get_language(language), query)
    except QueryError as exc:
        raise RuleError(f"invalid tree-sitter query: {exc}") from exc


def validate_rule(rule: Rule) -> None:
    """Raise RuleError if the rule cannot be executed deterministically."""
    if rule.type not in RULE_TYPES:
        raise RuleError(f"unknown rule type {rule.type!r}; expected one of {RULE_TYPES}")
    if rule.type == "analysis":
        from .analyzers import ANALYZERS

        if not rule.analyzer:
            raise RuleError("analysis rules need an analyzer name")
        if rule.analyzer not in ANALYZERS:
            raise RuleError(
                f"unknown analyzer {rule.analyzer!r}; "
                f"available: {', '.join(sorted(ANALYZERS))}"
            )
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
        language = resolve_dialect(code, language)
        applicable = rule_languages(language)
        tree = parse(code, language)
        report = CheckReport(language=language, syntax_ok=not tree.root_node.has_error)
        root = tree.root_node
        for rule in rules:
            if rule.language not in applicable:
                continue
            validate_rule(rule)
            try:
                violations = self._check_rule(rule, root, language)
            except RuleError:
                # query references a node type the dialect grammar lacks
                report.skipped_rules.append(rule.id)
                continue
            report.checked_rules.append(rule.id)
            report.violations.extend(violations)
        # deterministic ordering: by position, then rule id
        report.violations.sort(
            key=lambda v: (v.start_line, v.start_col, v.rule_id)
        )
        return report

    def _check_rule(self, rule: Rule, root: Node, language: str) -> list[RuleViolation]:
        if rule.type == "analysis":
            from .analyzers import get_analyzer

            return get_analyzer(rule.analyzer or "")(root, rule, language)
        # compile against the grammar the tree was parsed with, which may be
        # a dialect of rule.language (e.g. typescript rule on a tsx tree)
        query = _compile(language, rule.query)
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
            scope_query = _compile(language, rule.scope_query or "")
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
