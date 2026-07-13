"""Built-in deterministic analyzers (rule type ``analysis``).

Checks that a single tree-sitter query cannot express — set comparison,
clustering across nodes — implemented as pure functions of the parsed
AST. No LLM, no I/O, no randomness: for a given (code, rule) pair the
result is always identical, same as the query-based rule types.

An analyzer takes (root, rule, language) and returns violations. Register
new analyzers in ``ANALYZERS``; rules reference them by name via
``Rule.analyzer``.
"""

from __future__ import annotations

from typing import Callable, Iterator

from tree_sitter import Node

from .rules import Rule, RuleViolation, _violation


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _union_leaves(node: Node) -> list[Node]:
    """Flatten a (possibly nested) union_type into its member type nodes."""
    leaves = []
    for child in node.named_children:
        if child.type == "union_type":
            leaves.extend(_union_leaves(child))
        else:
            leaves.append(child)
    return leaves


def _object_keys(obj: Node) -> set[str]:
    keys = set()
    for entry in obj.named_children:
        if entry.type == "pair":
            key = entry.child_by_field_name("key")
            if key is not None:
                keys.add(_text(key))
        elif entry.type in ("shorthand_property_identifier", "shorthand_property_identifier_pattern"):
            keys.add(_text(entry))
    return keys


def _typed_object_literals(root: Node) -> Iterator[tuple[str, Node]]:
    """(type name, object node) for every literal explicitly typed as an
    interface: ``const x: I = {...}``, ``{...} satisfies I``, ``{...} as I``."""
    for node in _walk(root):
        if node.type == "variable_declarator":
            annotation = node.child_by_field_name("type")
            value = node.child_by_field_name("value")
            if annotation is None or value is None or value.type != "object":
                continue
            type_id = next(
                (c for c in annotation.named_children if c.type == "type_identifier"),
                None,
            )
            if type_id is not None:
                yield _text(type_id), value
        elif node.type in ("satisfies_expression", "as_expression"):
            children = node.named_children
            if (
                len(children) >= 2
                and children[0].type == "object"
                and children[-1].type == "type_identifier"
            ):
                yield _text(children[-1]), children[0]


def optional_variant_bag(root: Node, rule: Rule, language: str) -> list[RuleViolation]:
    """Flag interfaces that merge several variants behind optional properties.

    Candidates: interfaces with >= 2 optional properties. Two independent,
    deterministic pieces of evidence turn a candidate into a violation:

    Signal A (declaration): the interface also has a required property typed
    as a union of literal types — a discriminant key already exists, so the
    optionals belong inside per-variant union members.

    Signal B (usage): object literals in the same file typed as the interface
    fill the optional keys in >= 2 pairwise-disjoint groups — proof the
    optionals model exclusive variants, not independently-missing data.

    No evidence -> no violation: optional properties are legitimate.
    """
    violations = []
    for iface in _walk(root):
        if iface.type != "interface_declaration":
            continue
        name_node = iface.child_by_field_name("name")
        body = iface.child_by_field_name("body")
        if name_node is None or body is None:
            continue
        iface_name = _text(name_node)

        optional: list[str] = []
        required: list[tuple[str, Node]] = []
        for prop in body.named_children:
            if prop.type != "property_signature":
                continue
            prop_name = prop.child_by_field_name("name")
            if prop_name is None:
                continue
            if any(child.type == "?" for child in prop.children):
                optional.append(_text(prop_name))
            else:
                required.append((_text(prop_name), prop))
        if len(optional) < 2:
            continue
        optional_names = set(optional)

        # Signal A: a required literal-union property is a discriminant key
        discriminant = None
        for req_name, prop in required:
            annotation = prop.child_by_field_name("type")
            if annotation is None:
                continue
            union = next(
                (c for c in annotation.named_children if c.type == "union_type"),
                None,
            )
            if union is None:
                continue
            leaves = _union_leaves(union)
            if leaves and all(leaf.type == "literal_type" for leaf in leaves):
                discriminant = req_name
                break

        # Signal B: usages fill the optionals in disjoint groups
        used_groups = {
            frozenset(keys & optional_names)
            for type_name, obj in _typed_object_literals(root)
            if type_name == iface_name
            for keys in [_object_keys(obj)]
            if keys & optional_names
        }
        disjoint_split = len(used_groups) >= 2 and all(
            a.isdisjoint(b) for a in used_groups for b in used_groups if a is not b
        )

        if discriminant is None and not disjoint_split:
            continue

        evidence = []
        if discriminant is not None:
            evidence.append(
                f"discriminant-candidate key '{discriminant}' already exists"
            )
        if disjoint_split:
            groups = " / ".join(
                "{" + ", ".join(sorted(group)) + "}" for group in sorted(
                    used_groups, key=lambda g: sorted(g)
                )
            )
            evidence.append(f"usages fill disjoint optional-key groups {groups}")
        violations.append(
            _violation(
                rule,
                name_node,
                f"interface '{iface_name}' hides variants behind "
                f"{len(optional)} optional properties ({', '.join(sorted(optional_names))}) "
                f"— {'; '.join(evidence)}; split it into a discriminated union "
                f"so each variant's data is inferred from the key",
            )
        )
    return violations


ANALYZERS: dict[str, Callable[[Node, Rule, str], list[RuleViolation]]] = {
    "optional-variant-bag": optional_variant_bag,
}


def get_analyzer(name: str) -> Callable[[Node, Rule, str], list[RuleViolation]]:
    if name not in ANALYZERS:
        raise KeyError(name)
    return ANALYZERS[name]
