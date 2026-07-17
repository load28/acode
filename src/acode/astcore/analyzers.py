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


def _annotates_string_keyed_map(annotation: Node) -> bool:
    """True for ``Record<string, V>`` or ``{ [k: string]: V }`` annotations."""
    for node in annotation.named_children:
        if node.type == "generic_type":
            named = node.named_children
            if (
                len(named) >= 2
                and named[0].type == "type_identifier"
                and _text(named[0]) == "Record"
                and named[1].type == "type_arguments"
            ):
                args = named[1].named_children
                if args and args[0].type == "predefined_type" and _text(args[0]) == "string":
                    return True
        elif node.type == "object_type":
            for member in node.named_children:
                if member.type != "index_signature":
                    continue
                key_type = next(
                    (c for c in member.named_children if c.type == "predefined_type"),
                    None,
                )
                if key_type is not None and _text(key_type) == "string":
                    return True
    return False


def _static_object_keys(obj: Node) -> list[str] | None:
    """Key names if the literal enumerates a closed set: only plain
    identifier / string keys. Spread or computed keys mean the set may be
    open -> None. An empty literal is a dynamic accumulator -> None."""
    keys = []
    for entry in obj.named_children:
        if entry.type != "pair":
            return None  # spread_element, method, ...
        key = entry.child_by_field_name("key")
        if key is None or key.type not in ("property_identifier", "string"):
            return None  # computed_property_name etc.
        keys.append(_text(key).strip("'\""))
    return keys or None


def _has_dynamic_key_write(root: Node, var_name: str) -> bool:
    """True if the file writes ``var_name[<non-literal>] = ...`` — evidence
    the map is genuinely dynamic, so open string keys are legitimate."""
    for node in _walk(root):
        if node.type not in ("assignment_expression", "augmented_assignment_expression"):
            continue
        left = node.child_by_field_name("left")
        if left is None or left.type != "subscript_expression":
            continue
        obj = left.child_by_field_name("object")
        index = left.child_by_field_name("index")
        if (
            obj is not None
            and obj.type == "identifier"
            and _text(obj) == var_name
            and index is not None
            and index.type != "string"
        ):
            return True
    return False


def record_key_inference(root: Node, rule: Rule, language: str) -> list[RuleViolation]:
    """Flag string-keyed map annotations contradicted by their initializer.

    A variable annotated ``Record<string, V>`` (or ``{ [k: string]: V }``)
    but initialized with a closed set of literal keys should derive its key
    type instead (``keyof typeof`` over an ``as const`` object, or an
    existing union). Silent when the literal is empty, contains spread /
    computed keys, or the file writes to the map with a dynamic key — those
    are genuinely open maps where ``string`` is the honest type.
    """
    violations = []
    for decl in _walk(root):
        if decl.type != "variable_declarator":
            continue
        name_node = decl.child_by_field_name("name")
        annotation = decl.child_by_field_name("type")
        value = decl.child_by_field_name("value")
        if (
            name_node is None
            or annotation is None
            or value is None
            or value.type != "object"
            or name_node.type != "identifier"
            or not _annotates_string_keyed_map(annotation)
        ):
            continue
        keys = _static_object_keys(value)
        if keys is None:
            continue
        var_name = _text(name_node)
        if _has_dynamic_key_write(root, var_name):
            continue
        violations.append(
            _violation(
                rule,
                annotation,
                f"'{var_name}' is typed with open string keys but initialized "
                f"with a closed set ({', '.join(keys)}) — derive the key type "
                f"instead: `as const` + `keyof typeof`, or an existing union type",
            )
        )
    return violations


def _pair_value(entry: Node) -> Node | None:
    if entry.type != "pair":
        return None
    return entry.child_by_field_name("value")


def boolean_variant_bag(root: Node, rule: Rule, language: str) -> list[RuleViolation]:
    """Flag interfaces whose boolean flags behave as exclusive states.

    Candidates: interfaces with >= 2 boolean properties. Evidence comes
    from usage only — object literals in the file typed as the interface
    (``: I``, ``satisfies I``, ``as I``):

    - a flag ever assigned a non-literal value -> exclusivity is unprovable
      -> silence;
    - two flags ever true in the same literal -> the flags are independent
      -> silence;
    - otherwise, >= 2 distinct flags each appearing as the sole true flag
      of some usage prove the booleans model exclusive states -> violation,
      pointing at a status literal-union instead.
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

        flags = set()
        for prop in body.named_children:
            if prop.type != "property_signature":
                continue
            prop_name = prop.child_by_field_name("name")
            annotation = prop.child_by_field_name("type")
            if prop_name is None or annotation is None:
                continue
            is_boolean = any(
                c.type == "predefined_type" and _text(c) == "boolean"
                for c in annotation.named_children
            )
            if is_boolean:
                flags.add(_text(prop_name))
        if len(flags) < 2:
            continue

        true_sets = []
        provable = True
        for type_name, obj in _typed_object_literals(root):
            if type_name != iface_name:
                continue
            true_flags = set()
            for entry in obj.named_children:
                if (
                    entry.type in ("shorthand_property_identifier", "spread_element")
                    and (entry.type == "spread_element" or _text(entry) in flags)
                ):
                    provable = False  # variable/spread flag value: unprovable
                    break
                key = entry.child_by_field_name("key") if entry.type == "pair" else None
                if key is None or _text(key) not in flags:
                    continue
                value = _pair_value(entry)
                if value is None or value.type not in ("true", "false"):
                    provable = False  # dynamic flag value: exclusivity unprovable
                    break
                if value.type == "true":
                    true_flags.add(_text(key))
            if not provable or len(true_flags) >= 2:
                provable = False  # co-occurring true flags: independent booleans
                break
            true_sets.append(frozenset(true_flags))
        if not provable:
            continue

        sole_true = {next(iter(s)) for s in true_sets if len(s) == 1}
        if len(sole_true) < 2:
            continue
        violations.append(
            _violation(
                rule,
                name_node,
                f"interface '{iface_name}' models exclusive states with boolean "
                f"flags ({', '.join(sorted(sole_true))} are never true together "
                f"and each appears as the sole true flag) — replace them with a "
                f"single literal-union status key",
            )
        )
    return violations


def _identifier_used_outside_calls(root: Node, name: str, decl: Node) -> bool:
    """True if ``name`` is referenced anywhere except as the callee of a
    call or its own declaration — evidence of indirect calls we cannot see."""
    for node in _walk(root):
        if node.type != "identifier" or _text(node) != name:
            continue
        parent = node.parent
        if parent is None:
            continue
        if parent.type == "call_expression" and parent.child_by_field_name("function") == node:
            continue
        if parent == decl:  # the declaration's own name
            continue
        return True
    return False


def stringly_literal_param(root: Node, rule: Rule, language: str) -> list[RuleViolation]:
    """Flag ``string`` parameters that every call site fills from a closed
    literal set.

    Candidates: non-exported function declarations with a parameter typed
    exactly ``string``. Evidence: >= 2 direct calls in the file, every call
    passes that argument as a string literal, and >= 2 distinct values occur
    — the parameter's domain is a closed union the type should carry.
    Silence when the function is exported (outside callers are invisible),
    when its identifier is referenced outside direct calls (indirect calls),
    or when any call passes a non-literal / omits the argument.
    """
    violations = []
    for func in _walk(root):
        if func.type != "function_declaration":
            continue
        parent = func.parent
        if parent is not None and parent.type == "export_statement":
            continue
        name_node = func.child_by_field_name("name")
        params = func.child_by_field_name("parameters")
        if name_node is None or params is None:
            continue
        func_name = _text(name_node)
        if _identifier_used_outside_calls(root, func_name, func):
            continue

        string_params = []  # (index, param name node)
        positional = [
            p for p in params.named_children
            if p.type in ("required_parameter", "optional_parameter")
        ]
        for index, param in enumerate(positional):
            annotation = param.child_by_field_name("type")
            if annotation is None:
                continue
            named = annotation.named_children
            if len(named) == 1 and named[0].type == "predefined_type" and _text(named[0]) == "string":
                pattern = param.child_by_field_name("pattern")
                if pattern is not None and pattern.type == "identifier":
                    string_params.append((index, pattern))
        if not string_params:
            continue

        calls = [
            node.child_by_field_name("arguments")
            for node in _walk(root)
            if node.type == "call_expression"
            and (callee := node.child_by_field_name("function")) is not None
            and callee.type == "identifier"
            and _text(callee) == func_name
        ]
        if len(calls) < 2:
            continue

        for index, param_name in string_params:
            values = set()
            closed = True
            for arguments in calls:
                args = arguments.named_children if arguments is not None else []
                if index >= len(args) or args[index].type != "string":
                    closed = False
                    break
                values.add(_text(args[index]).strip("'\""))
            if closed and len(values) >= 2:
                union = " | ".join(f"'{v}'" for v in sorted(values))
                violations.append(
                    _violation(
                        rule,
                        param_name,
                        f"parameter '{_text(param_name)}' of '{func_name}' is "
                        f"typed string but every call passes one of {union} — "
                        f"hold the values in an `as const` object and type the "
                        f"parameter with the derived union "
                        f"(`type T = typeof Obj[keyof typeof Obj]`)",
                    )
                )
    return violations


def duplicate_literal_union(root: Node, rule: Rule, language: str) -> list[RuleViolation]:
    """Flag literal unions repeated inline instead of extracted to an alias.

    Collects every top-level union type whose members are all literal types,
    keyed by the (order-insensitive) member set. A set occurring >= 2 times
    flags each *inline* occurrence; occurrences that are the body of a
    ``type X = ...`` alias are never flagged — when one exists its name is
    suggested, otherwise the fix is the repo's enum-replacement shape: an
    ``as const`` object holding the values plus the derived union type.
    """
    unions: dict[frozenset, list[tuple[Node, bool, str | None]]] = {}
    for node in _walk(root):
        if node.type != "union_type":
            continue
        parent = node.parent
        if parent is not None and parent.type == "union_type":
            continue  # only the outermost union node
        leaves = _union_leaves(node)
        if not leaves or not all(leaf.type == "literal_type" for leaf in leaves):
            continue
        key = frozenset(_text(leaf) for leaf in leaves)
        alias_name = None
        is_alias = parent is not None and parent.type == "type_alias_declaration"
        if is_alias:
            alias_id = parent.child_by_field_name("name")
            alias_name = _text(alias_id) if alias_id is not None else None
        unions.setdefault(key, []).append((node, is_alias, alias_name))

    violations = []
    for key, occurrences in unions.items():
        if len(occurrences) < 2:
            continue
        alias = next((name for _, is_alias, name in occurrences if is_alias and name), None)
        members = " | ".join(sorted(key))
        for node, is_alias, _name in occurrences:
            if is_alias:
                continue
            if alias:
                fix = f"use the existing alias '{alias}'"
            else:
                fix = (
                    "hold the values in an `as const` object and derive the "
                    "union (`type T = typeof Obj[keyof typeof Obj]`)"
                )
            violations.append(
                _violation(
                    rule,
                    node,
                    f"literal union {members} appears {len(occurrences)} times "
                    f"in this file — {fix} so the set has one source of truth",
                )
            )
    return violations


_LITERAL_VALUE_TYPES = ("string", "number", "true", "false")


def _is_module_level_const(declarator: Node) -> bool:
    decl = declarator.parent
    if decl is None or decl.type != "lexical_declaration":
        return False
    if not any(child.type == "const" for child in decl.children):
        return False
    parent = decl.parent
    if parent is None:
        return False
    if parent.type == "program":
        return True
    return parent.type == "export_statement" and (
        parent.parent is not None and parent.parent.type == "program"
    )


def _has_direct_mutation(root: Node, var_name: str) -> bool:
    """True if the file reassigns ``var_name``, writes/deletes one of its
    properties, or passes it as Object.assign's first argument."""
    for node in _walk(root):
        if node.type in ("assignment_expression", "augmented_assignment_expression"):
            left = node.child_by_field_name("left")
            if left is None:
                continue
            if left.type == "identifier" and _text(left) == var_name:
                return True
            if left.type in ("member_expression", "subscript_expression"):
                obj = left.child_by_field_name("object")
                if obj is not None and obj.type == "identifier" and _text(obj) == var_name:
                    return True
        elif node.type == "unary_expression":
            if any(c.type == "delete" for c in node.children):
                operand = node.child_by_field_name("argument")
                if operand is not None and operand.type in ("member_expression", "subscript_expression"):
                    obj = operand.child_by_field_name("object")
                    if obj is not None and obj.type == "identifier" and _text(obj) == var_name:
                        return True
        elif node.type == "call_expression":
            callee = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            if (
                callee is not None
                and callee.type == "member_expression"
                and _text(callee) == "Object.assign"
                and arguments is not None
            ):
                args = arguments.named_children
                if args and args[0].type == "identifier" and _text(args[0]) == var_name:
                    return True
    return False


def as_const_candidate(root: Node, rule: Rule, language: str) -> list[RuleViolation]:
    """Flag module-level const object literals that should be ``as const``.

    Candidates: unannotated ``const X = { ... }`` at module level whose
    entries are all plain identifier/string keys with primitive literal
    values (string / number / boolean). Evidence of immutability: the file
    never reassigns X, writes or deletes a property, or hands X to
    ``Object.assign`` — then ``as const`` makes the literal's exact shape
    available (``keyof typeof`` keys, literal value types) for free.
    Mutation through aliases or callees is invisible — documented limit.
    """
    violations = []
    for declarator in _walk(root):
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        value = declarator.child_by_field_name("value")
        if (
            name_node is None
            or name_node.type != "identifier"
            or declarator.child_by_field_name("type") is not None
            or value is None
            or value.type != "object"
            or not _is_module_level_const(declarator)
        ):
            continue
        keys = _static_object_keys(value)
        if keys is None:
            continue
        if not all(
            (v := _pair_value(entry)) is not None and v.type in _LITERAL_VALUE_TYPES
            for entry in value.named_children
        ):
            continue
        var_name = _text(name_node)
        if _has_direct_mutation(root, var_name):
            continue
        violations.append(
            _violation(
                rule,
                name_node,
                f"'{var_name}' is a never-mutated literal constant — freeze it "
                f"with `as const` so its keys and values become precise types "
                f"(`keyof typeof {var_name}` for the key union)",
            )
        )
    return violations


def _as_const_string_members(root: Node) -> dict[str, dict[str, str]]:
    """Map ``X = { K: 'v', ... } as const`` declarations to {value: member}.

    Only identifier keys with string-literal values qualify — anything else
    makes the ``X.Member`` suggestion unsound, so the object is dropped.
    """
    objects: dict[str, dict[str, str]] = {}
    for declarator in _walk(root):
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        value = declarator.child_by_field_name("value")
        if (
            name_node is None
            or name_node.type != "identifier"
            or value is None
            or value.type != "as_expression"
            or not any(c.type == "const" for c in value.children)
        ):
            continue
        obj = next((c for c in value.named_children if c.type == "object"), None)
        if obj is None:
            continue
        members: dict[str, str] = {}
        for entry in obj.named_children:
            key = entry.child_by_field_name("key") if entry.type == "pair" else None
            val = _pair_value(entry)
            if key is None or key.type != "property_identifier" or val is None or val.type != "string":
                members = {}
                break
            members.setdefault(_text(val).strip("'\""), _text(key))
        if members:
            objects[_text(name_node)] = members
    return objects


def _derived_union_aliases(root: Node, objects: dict[str, dict[str, str]]) -> dict[str, str]:
    """Map ``type T = typeof X[keyof typeof X]`` aliases to their object X."""
    aliases: dict[str, str] = {}
    for alias in _walk(root):
        if alias.type != "type_alias_declaration":
            continue
        name = alias.child_by_field_name("name")
        value = alias.child_by_field_name("value")
        if name is None or value is None or value.type != "lookup_type":
            continue
        named = value.named_children
        if len(named) != 2 or named[0].type != "type_query" or named[1].type != "index_type_query":
            continue
        outer = named[0].named_children
        inner_query = named[1].named_children
        if len(outer) != 1 or outer[0].type != "identifier":
            continue
        if len(inner_query) != 1 or inner_query[0].type != "type_query":
            continue
        inner = inner_query[0].named_children
        if len(inner) != 1 or inner[0].type != "identifier":
            continue
        obj_name = _text(outer[0])
        if obj_name == _text(inner[0]) and obj_name in objects:
            aliases[_text(name)] = obj_name
    return aliases


def _annotated_alias_name(node: Node) -> str | None:
    """The bare type-identifier a node's ``type`` annotation names, if any."""
    annotation = node.child_by_field_name("type")
    if annotation is None:
        return None
    named = annotation.named_children
    if len(named) == 1 and named[0].type == "type_identifier":
        return _text(named[0])
    return None


def constant_callsite(root: Node, rule: Rule, language: str) -> list[RuleViolation]:
    """Flag raw string literals where a value typed by a union derived from
    an ``as const`` object is expected — call arguments, variable
    initializers, and parameter defaults should reference the object's
    member instead, so value changes stay in one place.

    The full evidence chain must be visible in the file: the as const
    object (identifier keys, string values), the derived alias
    ``type T = typeof X[keyof typeof X]``, and a use site annotated ``T``
    — a file-local function's parameter fed by a direct call, a typed
    variable declarator, or a parameter default — receiving a string
    literal that matches one of X's values. Literals outside X's values
    are the compiler's job (type error) and stay silent.
    """
    objects = _as_const_string_members(root)
    if not objects:
        return []
    aliases = _derived_union_aliases(root, objects)
    if not aliases:
        return []

    func_params: dict[str, dict[int, str]] = {}
    for func in _walk(root):
        if func.type != "function_declaration":
            continue
        name_node = func.child_by_field_name("name")
        params = func.child_by_field_name("parameters")
        if name_node is None or params is None:
            continue
        positional = [
            p for p in params.named_children
            if p.type in ("required_parameter", "optional_parameter")
        ]
        mapping = {}
        for index, param in enumerate(positional):
            type_name = _annotated_alias_name(param)
            if type_name in aliases:
                mapping[index] = type_name
        if mapping:
            func_params[_text(name_node)] = mapping

    violations = []
    for node in _walk(root):
        if node.type not in ("variable_declarator", "required_parameter", "optional_parameter"):
            continue
        type_name = _annotated_alias_name(node)
        if type_name not in aliases:
            continue
        value = node.child_by_field_name("value")
        if value is None or value.type != "string":
            continue
        raw = _text(value).strip("'\"")
        obj_name = aliases[type_name]
        member = objects[obj_name].get(raw)
        if member is None:
            continue
        site = "a variable" if node.type == "variable_declarator" else "a parameter default"
        violations.append(
            _violation(
                rule,
                value,
                f"raw literal '{raw}' initializes {site} typed '{type_name}' — "
                f"reference the constant `{obj_name}.{member}` so value changes "
                f"stay in one place",
            )
        )
    for call in _walk(root):
        if call.type != "call_expression":
            continue
        callee = call.child_by_field_name("function")
        arguments = call.child_by_field_name("arguments")
        if callee is None or callee.type != "identifier" or arguments is None:
            continue
        mapping = func_params.get(_text(callee))
        if not mapping:
            continue
        args = arguments.named_children
        for index, type_name in mapping.items():
            if index >= len(args) or args[index].type != "string":
                continue
            raw = _text(args[index]).strip("'\"")
            obj_name = aliases[type_name]
            member = objects[obj_name].get(raw)
            if member is None:
                continue
            violations.append(
                _violation(
                    rule,
                    args[index],
                    f"raw literal '{raw}' passed where the parameter is typed "
                    f"'{type_name}' — reference the constant `{obj_name}.{member}` "
                    f"so value changes stay in one place",
                )
            )
    return violations


ANALYZERS: dict[str, Callable[[Node, Rule, str], list[RuleViolation]]] = {
    "optional-variant-bag": optional_variant_bag,
    "record-key-inference": record_key_inference,
    "boolean-variant-bag": boolean_variant_bag,
    "stringly-literal-param": stringly_literal_param,
    "duplicate-literal-union": duplicate_literal_union,
    "as-const-candidate": as_const_candidate,
    "constant-callsite": constant_callsite,
}


def get_analyzer(name: str) -> Callable[[Node, Rule, str], list[RuleViolation]]:
    if name not in ANALYZERS:
        raise KeyError(name)
    return ANALYZERS[name]
