"""Vue 3 front-end for the cross-file data-flow analysis (see flow.py).

This module knows Vue's syntax and idioms and extracts them into the same
framework-neutral model the React front-end uses — so chain building,
derived-value provenance, the composable fixpoint, and the generic checkers
are shared, while everything Vue lives here:

    SFC              a .vue file = one component; the <script setup> block
                     is parsed with the typescript grammar through a
                     position-preserving view (non-script lines blanked),
                     the <template> block through a small deterministic tag
                     tokenizer (quote/comment aware)
    props            defineProps — type argument (inline object type or a
                     same-file interface/type alias), object arg, array
                     arg, withDefaults(...), destructured (3.5)
    state            ref/shallowRef/reactive -> local-state (no setter:
                     mutation is `x.value = ...`)
    server origin    an assignment TARGET inside a fetchy scope — lifecycle
                     /watch callbacks or a top-level statement containing a
                     fetch-like call. Stricter than React's referenced-
                     setter rule on purpose: a ref that is only an INPUT to
                     the fetch (`watch(user, () => fetch(url(user.value)))`)
                     must not be promoted.
    query            vue-query useQuery family, Nuxt useFetch/useAsyncData,
                     useSWRV -> query (sanctioned server state)
    context          inject -> context. provide() needs no special-casing:
                     it is not a render edge, so provided values never form
                     chains (the React side has to skip <X.Provider>).
    composables      use* functions (in .vue or .ts/.js files) run through
                     the shared hook fixpoint; a ref handed INTO a
                     composable whose parameter is fetch-written is
                     promoted (Vue passes the ref itself, not a setter)
    mutation intent  v-model on a child component, and @event listeners
                     whose expression assigns a state ref (or calls a
                     script function that does) — recorded as mutation
                     edges, the Vue analog of a drilled setter

Determinism holds exactly as for React: same files, same findings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tree_sitter import Node

from .flow import (
    Binding,
    ComponentFacts,
    CUSTOM_HOOK_NAME,
    DEFAULT_FETCH_NAMES,
    FUNCTION_NODE_TYPES,
    FileFacts,
    HookCall,
    HookFacts,
    ProjectAnalysis,
    PropPass,
    SEMANTIC_CHECKS,
    SemanticFinding,
    apply_hook_returns,
    build_chains,
    call_callee_root,
    check_prop_drilling,
    check_server_state_drilling,
    expr_candidates,
    extract_imports,
    extract_returns_spec,
    function_body,
    note_prop,
    parameter_nodes,
    pattern_targets,
    resolve_components,
    resolve_hooks,
    root_identifier,
    split_virtual_files,
    text,
    top_level_functions,
    walk,
)
from .parser import language_for_path, parse

__all__ = [
    "VUE_LANGUAGES", "analyze_project", "analyze_source",
    "extract_vue_file_facts", "scan_template_tags", "script_only_view",
    "split_sfc",
]

VUE_LANGUAGES = ("vue",)

_STATE_FNS = {"ref", "shallowRef", "reactive", "shallowReactive"}

_QUERY_FNS = {
    "useQuery", "useInfiniteQuery", "useSuspenseQuery",  # vue-query
    "useFetch", "useLazyFetch", "useAsyncData", "useLazyAsyncData",  # nuxt
    "useSWRV",
}

_EFFECT_FNS = {"onMounted", "onBeforeMount", "onActivated", "onUpdated",
               "watch", "watchEffect", "watchPostEffect", "watchSyncEffect"}

# use*-named builtins that must not be resolved as project composables
_BUILTIN_USE_FNS = _QUERY_FNS | {"useSlots", "useAttrs", "useTemplateRef",
                                 "useId", "useModel"}

_FORWARD_ORIGINS = {name: "query" for name in _QUERY_FNS}

# built-in template tags that look like components but end a chain
_BUILTIN_TAGS = {
    "template", "slot", "component", "transition", "keep-alive",
    "transition-group", "teleport", "suspense", "router-view", "router-link",
}


@dataclass
class VueComponentFacts(ComponentFacts):
    """Vue extension of the shared model: (child, state identifier) pairs
    where the child mutates the state from below (v-model / @event)."""

    mutation_edges: list[tuple[str, str]] = field(default_factory=list)


# ------------------------------------------------------------- SFC layout


_SCRIPT_OPEN = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_TEMPLATE_OPEN = re.compile(r"<template\b[^>]*>", re.IGNORECASE)


def _script_spans(code: str) -> list[tuple[int, int]]:
    spans = []
    pos = 0
    while True:
        match = _SCRIPT_OPEN.search(code, pos)
        if match is None:
            return spans
        close = code.find("</script>", match.end())
        if close == -1:
            close = len(code)
        spans.append((match.end(), close))
        pos = close + 1


def script_only_view(code: str) -> str:
    """The SFC with everything outside <script> content blanked out —
    newlines kept, other characters replaced by spaces — so the typescript
    grammar parses it with line/column positions matching the file."""
    spans = _script_spans(code)
    if not spans:
        return re.sub(r"[^\n]", " ", code)
    out = []
    for i, ch in enumerate(code):
        if ch == "\n":
            out.append("\n")
        elif any(start <= i < end for start, end in spans):
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)


def split_sfc(code: str) -> tuple[str, str, int]:
    """(script_view, template_content, template_line_offset). The script
    view preserves file positions; the template offset is the number of
    lines before the template content starts."""
    template = ""
    offset = 0
    match = _TEMPLATE_OPEN.search(code)
    if match is not None:
        close = code.rfind("</template>")
        if close > match.end():
            template = code[match.end():close]
            offset = code.count("\n", 0, match.end())
    return script_only_view(code), template, offset


# -------------------------------------------------------- template scanner


@dataclass
class TemplateAttr:
    name: str
    value: str | None
    line: int  # 1-based, file coordinates
    col: int


@dataclass
class TemplateTag:
    name: str
    attrs: list[TemplateAttr]
    line: int
    col: int


def scan_template_tags(template: str, line_offset: int = 0,
                       ) -> list[TemplateTag]:
    """Deterministic tokenizer for opening tags and their attributes.
    Quote-aware (a `>` inside an attribute value does not end the tag),
    skips comments and closing tags. Not a full HTML parser — Vue
    directives are attribute strings either way."""
    tags: list[TemplateTag] = []
    i, line, col = 0, 1 + line_offset, 0
    n = len(template)

    def advance(to: int) -> None:
        nonlocal i, line, col
        to = min(to, n)
        while i < to:
            if template[i] == "\n":
                line += 1
                col = 0
            else:
                col += 1
            i += 1

    while i < n:
        if template[i] != "<":
            advance(i + 1)
            continue
        if template.startswith("<!--", i):
            end = template.find("-->", i)
            advance(n if end == -1 else end + 3)
            continue
        j = i + 1
        if j >= n or not template[j].isalpha():
            if j < n and template[j] == "/":  # closing tag
                end = template.find(">", j)
                advance(n if end == -1 else end + 1)
            else:
                advance(i + 1)
            continue
        tag_line, tag_col = line, col
        advance(j)
        k = i
        while k < n and (template[k].isalnum() or template[k] in "-_."):
            k += 1
        name = template[i:k]
        advance(k)
        attrs: list[TemplateAttr] = []
        while i < n:
            while i < n and template[i] in " \t\r\n":
                advance(i + 1)
            if i >= n or template[i] == ">":
                advance(i + 1)
                break
            if template.startswith("/>", i):
                advance(i + 2)
                break
            if template[i] == "/":
                advance(i + 1)
                continue
            attr_line, attr_col = line, col
            k = i
            while k < n and template[k] not in " \t\r\n=>/":
                k += 1
            if k == i:  # stray character; don't loop forever
                advance(i + 1)
                continue
            attr_name = template[i:k]
            advance(k)
            value: str | None = None
            while i < n and template[i] in " \t\r\n":
                advance(i + 1)
            if i < n and template[i] == "=":
                advance(i + 1)
                while i < n and template[i] in " \t\r\n":
                    advance(i + 1)
                if i < n and template[i] in "\"'":
                    quote = template[i]
                    advance(i + 1)
                    k = template.find(quote, i)
                    if k == -1:
                        k = n
                    value = template[i:k]
                    advance(k + 1)
                else:
                    k = i
                    while k < n and template[k] not in " \t\r\n>":
                        k += 1
                    value = template[i:k]
                    advance(k)
            attrs.append(TemplateAttr(attr_name, value, attr_line, attr_col))
        tags.append(TemplateTag(name, attrs, tag_line, tag_col))
    return tags


def _camelize(name: str) -> str:
    parts = name.split("-")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _pascalize(name: str) -> str:
    parts = re.split(r"[-_]", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _component_tag(tag_name: str) -> str | None:
    """PascalCase component name for a template tag, or None for host
    elements and Vue/router built-ins."""
    lowered = tag_name.lower()
    if lowered in _BUILTIN_TAGS:
        return None
    if "-" in tag_name:
        return _pascalize(tag_name)
    if tag_name[:1].isupper():
        return tag_name
    return None


# ------------------------------------------------------ script extraction


def _walk_skipping(node: Node, skip: set[int]):
    if node.id in skip:
        return
    yield node
    for child in node.named_children:
        yield from _walk_skipping(child, skip)


def _find_define_props(value: Node) -> Node | None:
    """The defineProps(...) call in a declarator value, unwrapping
    withDefaults(defineProps(...), {...})."""
    if value.type != "call_expression":
        return None
    callee = call_callee_root(value)
    if callee == "defineProps":
        return value
    if callee == "withDefaults":
        args = value.child_by_field_name("arguments")
        if args is not None:
            for arg in args.named_children:
                found = _find_define_props(arg)
                if found is not None:
                    return found
    return None


def _named_type_members(root: Node, type_name: str) -> list[str]:
    """Property names of a same-file `interface X { ... }` or
    `type X = { ... }`."""
    for node in root.named_children:
        target = node
        if node.type == "export_statement":
            decl = node.child_by_field_name("declaration")
            if decl is None:
                continue
            target = decl
        if target.type == "interface_declaration":
            name = target.child_by_field_name("name")
            body = target.child_by_field_name("body")
            if name is not None and text(name) == type_name and body is not None:
                return [text(sig_name) for sig in body.named_children
                        if sig.type == "property_signature"
                        and (sig_name := sig.child_by_field_name("name"))]
        elif target.type == "type_alias_declaration":
            name = target.child_by_field_name("name")
            value = target.child_by_field_name("value")
            if name is not None and text(name) == type_name \
                    and value is not None and value.type == "object_type":
                return [text(sig_name) for sig in value.named_children
                        if sig.type == "property_signature"
                        and (sig_name := sig.child_by_field_name("name"))]
    return []


def _define_props_names(call: Node, root: Node) -> list[str]:
    names: list[str] = []
    type_args = call.child_by_field_name("type_arguments")
    if type_args is not None:
        for t in type_args.named_children:
            if t.type == "object_type":
                for sig in t.named_children:
                    if sig.type == "property_signature":
                        sig_name = sig.child_by_field_name("name")
                        if sig_name is not None:
                            names.append(text(sig_name))
            elif t.type == "type_identifier":
                names.extend(_named_type_members(root, text(t)))
    args = call.child_by_field_name("arguments")
    if args is not None:
        for arg in args.named_children:
            if arg.type == "object":
                for pair in arg.named_children:
                    if pair.type == "pair":
                        key = pair.child_by_field_name("key")
                        if key is not None:
                            names.append(text(key))
            elif arg.type == "array":
                for entry in arg.named_children:
                    frag = next((n for n in entry.named_children
                                 if n.type == "string_fragment"), None)
                    if frag is not None:
                        names.append(text(frag))
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def _register_props(comp: ComponentFacts, names: list[str],
                    node: Node) -> None:
    line, col = node.start_point[0] + 1, node.start_point[1]
    for prop in names:
        if prop not in comp.props:
            comp.props.append(prop)
        comp.bindings.setdefault(prop, Binding(
            prop, "prop", line, col, prop_roots=(prop,)))


def _assignment_targets(scope_node: Node) -> set[str]:
    """Root identifiers assigned within a node (`x = ...`, `x.value = ...`,
    `x += ...`)."""
    targets: set[str] = set()
    for node in walk(scope_node):
        if node.type in ("assignment_expression", "augmented_assignment_expression"):
            left = node.child_by_field_name("left")
            if left is not None:
                root, _ = root_identifier(left)
                if root:
                    targets.add(root)
    return targets


def _scan_fetchy_scope(scope_node: Node, comp: ComponentFacts,
                       fetch_names: tuple[str, ...]) -> None:
    """If the scope performs a fetch-like call, its assignment targets are
    server-written: promote matching local-state refs and remember the
    names for composable parameter promotion."""
    has_fetch = any(
        node.type == "call_expression"
        and call_callee_root(node) in fetch_names
        for node in walk(scope_node))
    if not has_fetch:
        return
    targets = _assignment_targets(scope_node)
    comp.fetch_referenced |= targets
    for name in targets:
        binding = comp.bindings.get(name)
        if binding is not None and binding.origin == "local-state":
            binding.origin = "server-state"


def _extract_script_bindings(root: Node, comp: ComponentFacts,
                             fetch_names: tuple[str, ...],
                             skip: set[int]) -> None:
    """Declarations, composable calls, and fetchy scopes in a <script
    setup> body (or a composable body). `skip` holds node ids of nested
    composable definitions that get their own scope."""
    effects: list[Node] = []
    for node in _walk_skipping(root, skip):
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None or callee.type != "identifier":
            continue
        name = text(callee)
        if name in _EFFECT_FNS:
            args = node.child_by_field_name("arguments")
            if args is not None:
                effects.extend(a for a in args.named_children
                               if a.type in FUNCTION_NODE_TYPES)
            comp.hooks.add(name)
        elif name == "defineProps":
            # bare `defineProps([...])` — assigned forms are handled by the
            # declarator loop, which also sets props_param
            parent = node.parent
            in_declarator = False
            while parent is not None and parent.type != "program":
                if parent.type == "variable_declarator":
                    in_declarator = True
                    break
                parent = parent.parent
            if not in_declarator:
                _register_props(comp, _define_props_names(node, root), node)
        elif CUSTOM_HOOK_NAME.match(name) and name not in _BUILTIN_USE_FNS:
            args_node = node.child_by_field_name("arguments")
            arg_roots = tuple(
                root_identifier(a)[0] or ""
                for a in (args_node.named_children if args_node is not None else []))
            target_kind, target_names = None, ()
            parent = node.parent
            if parent is not None and parent.type == "variable_declarator" \
                    and parent.child_by_field_name("value") == node:
                name_node = parent.child_by_field_name("name")
                if name_node is not None:
                    target_kind, target_names = pattern_targets(name_node)
            comp.hook_calls.append(HookCall(
                hook=name, args=arg_roots,
                target_kind=target_kind, target_names=target_names,
                line=node.start_point[0] + 1, col=node.start_point[1]))
            comp.hooks.add(name)

    for decl in _walk_skipping(root, skip):
        if decl.type != "variable_declarator":
            continue
        name_node = decl.child_by_field_name("name")
        value = decl.child_by_field_name("value")
        if name_node is None or value is None:
            continue
        line, col = name_node.start_point[0] + 1, name_node.start_point[1]

        define_props = _find_define_props(value)
        if define_props is not None:
            names = _define_props_names(define_props, root)
            if name_node.type == "identifier":
                comp.props_param = text(name_node)
                _register_props(comp, names, name_node)
            else:  # destructured defineProps (3.5)
                _, targets = pattern_targets(name_node)
                _register_props(
                    comp, names or [key for key, _ in targets], name_node)
                for key, local in targets:
                    if local != key:
                        comp.bindings[local] = Binding(
                            local, "prop", line, col, prop_roots=(key,))
            continue

        callee_name = call_callee_root(value) \
            if value.type == "call_expression" else None

        if callee_name in _STATE_FNS:
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings[local] = Binding(local, "local-state", line, col)
            comp.hooks.add(callee_name)
            continue
        if callee_name in _QUERY_FNS:
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings[local] = Binding(local, "query", line, col)
            comp.hooks.add(callee_name)
            continue
        if callee_name == "inject":
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings[local] = Binding(local, "context", line, col)
            comp.hooks.add("inject")
            continue
        if callee_name == "computed":
            deps = expr_candidates(value, comp)
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings.setdefault(local, Binding(
                    local, "local", line, col, derived_from=deps))
            comp.hooks.add("computed")
            continue
        if callee_name in ("toRefs", "toRef"):
            args = value.child_by_field_name("arguments")
            arg_nodes = args.named_children if args is not None else []
            first = root_identifier(arg_nodes[0])[0] if arg_nodes else None
            if first is not None and first == comp.props_param:
                if callee_name == "toRefs":
                    _, targets = pattern_targets(name_node)
                    for key, local in targets:
                        note_prop(comp, key, name_node)
                        comp.bindings[local] = Binding(
                            local, "prop", line, col, prop_roots=(key,))
                elif name_node.type == "identifier" and len(arg_nodes) > 1:
                    frag = next((n for n in walk(arg_nodes[1])
                                 if n.type == "string_fragment"), None)
                    if frag is not None:
                        prop = text(frag)
                        note_prop(comp, prop, name_node)
                        comp.bindings[text(name_node)] = Binding(
                            text(name_node), "prop", line, col,
                            prop_roots=(prop,))
                continue
        if callee_name is not None and CUSTOM_HOOK_NAME.match(callee_name) \
                and callee_name not in _BUILTIN_USE_FNS:
            # placeholder bindings; overwritten when the composable resolves
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings.setdefault(local, Binding(local, "local", line, col))
            continue
        if value.type not in ("identifier", "member_expression"):
            deps = expr_candidates(value, comp)
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings.setdefault(local, Binding(
                    local, "local", line, col, derived_from=deps))
            continue
        # plain alias / destructure of an identifier or member chain
        root_name, path = root_identifier(value)
        if root_name is None:
            continue
        if comp.props_param is not None and root_name == comp.props_param:
            if name_node.type == "object_pattern" and not path:
                _, targets = pattern_targets(name_node)
                for key, local in targets:
                    note_prop(comp, key, name_node)
                    if local != key:
                        comp.bindings[local] = Binding(
                            local, "prop", line, col, prop_roots=(key,))
                continue
            if path:
                note_prop(comp, path[0], value)
                root_name = path[0]
        _, targets = pattern_targets(name_node)
        for _key, local in targets:
            comp.bindings.setdefault(local, Binding(
                local, "local", line, col, derived_from=(root_name,)))

    for effect in effects:
        _scan_fetchy_scope(effect, comp, fetch_names)
    # a top-level statement doing the fetch (`fetch(...).then(...)`) is the
    # setup body acting as its own effect
    for stmt in root.named_children:
        if stmt.type == "expression_statement" and stmt.id not in skip:
            _scan_fetchy_scope(stmt, comp, fetch_names)


def _function_assignments(root: Node, skip: set[int]) -> dict[str, set[str]]:
    """Top-level script functions -> the identifiers they assign, so
    `@save="onSave"` can be linked to the state onSave mutates."""
    out: dict[str, set[str]] = {}
    for name, _name_node, func in top_level_functions(root):
        if func.id in skip:
            continue
        body = function_body(func) or func
        out[name] = _assignment_targets(body)
    return out


# ------------------------------------------------------ template -> edges


def _template_expr(expr_str: str) -> Node | None:
    tree = parse(expr_str, "typescript")
    for stmt in tree.root_node.named_children:
        if stmt.type == "expression_statement" and stmt.named_children:
            return stmt.named_children[0]
    return None


def _template_sources(comp: ComponentFacts, expr: Node,
                      ) -> tuple[tuple[str, ...], bool]:
    root, path = root_identifier(expr)
    if root is not None and not root.startswith("$"):
        if comp.props_param is not None and root == comp.props_param:
            if not path:
                return (), False
            note_prop(comp, path[0], expr)
            return (path[0],), False
        return (root,), False
    candidates = tuple(c for c in expr_candidates(expr, comp)
                       if not c.startswith("$"))
    return candidates, True


def _apply_template(comp: VueComponentFacts, template: str, offset: int,
                    fn_assigns: dict[str, set[str]]) -> None:
    for tag in scan_template_tags(template, offset):
        child = _component_tag(tag.name)
        if child is None:
            continue
        for attr in tag.attrs:
            if attr.value is None:
                continue
            name = attr.name
            base = name.split(".")[0]  # strip modifiers
            if base.startswith("v-model"):
                arg = base.split(":", 1)[1] if ":" in base else "modelValue"
                expr = _template_expr(attr.value)
                if expr is None:
                    continue
                sources, derived = _template_sources(comp, expr)
                if not sources:
                    continue
                comp.passes.append(PropPass(
                    child=child, attr=_camelize(arg), sources=sources,
                    line=attr.line, col=attr.col, derived=derived))
                comp.mutation_edges.append((child, sources[0]))
            elif base.startswith(":") or base.startswith("v-bind:"):
                prop = base.split(":", 1)[1]
                expr = _template_expr(attr.value)
                if expr is None:
                    continue
                sources, derived = _template_sources(comp, expr)
                if not sources:
                    continue
                comp.passes.append(PropPass(
                    child=child, attr=_camelize(prop), sources=sources,
                    line=attr.line, col=attr.col, derived=derived))
            elif base == "v-bind":
                expr = _template_expr(attr.value)
                if expr is None:
                    continue
                root, path = root_identifier(expr)
                if root is not None and root == comp.props_param and not path:
                    comp.passes.append(PropPass(
                        child=child, attr="*", sources=(root,),
                        line=attr.line, col=attr.col, spread=True))
            elif base.startswith("@") or base.startswith("v-on:"):
                expr = _template_expr(attr.value)
                if expr is None:
                    continue
                for target in sorted(_assignment_targets(expr)):
                    comp.mutation_edges.append((child, target))
                for ident in expr_candidates(expr, comp):
                    for target in sorted(fn_assigns.get(ident, ())):
                        comp.mutation_edges.append((child, target))


# --------------------------------------------------------------- analysis


def component_name_for(path: str) -> str:
    return _pascalize(Path(path).stem)


def _extract_composables(script_root: Node, path: str,
                         fetch_names: tuple[str, ...],
                         ) -> tuple[dict[str, HookFacts], set[int]]:
    hooks: dict[str, HookFacts] = {}
    skip: set[int] = set()
    for name, name_node, func in top_level_functions(script_root):
        if not CUSTOM_HOOK_NAME.match(name):
            continue
        skip.add(func.id)
        body = function_body(func) or func
        scope = ComponentFacts(
            name=name, file=path,
            line=name_node.start_point[0] + 1, col=name_node.start_point[1])
        params = [text(n) for n in parameter_nodes(func)
                  if n.type == "identifier"]
        scope.params = tuple(params)
        for param in params:
            scope.bindings.setdefault(param, Binding(
                param, "local", func.start_point[0] + 1, func.start_point[1]))
        _extract_script_bindings(body, scope, fetch_names, set())
        scope.returns_spec = extract_returns_spec(func, body)
        hooks[name] = HookFacts(
            name=name, file=path,
            line=name_node.start_point[0] + 1, scope=scope)
    return hooks, skip


def extract_vue_file_facts(path: str, code: str,
                           fetch_names: tuple[str, ...] = DEFAULT_FETCH_NAMES,
                           ) -> FileFacts:
    """One .vue SFC -> one component (named after the file) plus any
    composables defined in its script."""
    script_view, template, template_offset = split_sfc(code)
    tree = parse(script_view, "typescript")
    root = tree.root_node
    facts = FileFacts(path=path, language="vue",
                      syntax_ok=not root.has_error)
    facts.imports = extract_imports(root)
    facts.hooks, skip = _extract_composables(root, path, fetch_names)

    comp = VueComponentFacts(
        name=component_name_for(path), file=path, line=1, col=0)
    _extract_script_bindings(root, comp, fetch_names, skip)
    fn_assigns = _function_assignments(root, skip)
    _apply_template(comp, template, template_offset, fn_assigns)
    facts.components[comp.name] = comp
    return facts


def extract_composables_file(path: str, code: str, language: str,
                             fetch_names: tuple[str, ...] = DEFAULT_FETCH_NAMES,
                             ) -> FileFacts:
    """A plain .ts/.js file in a Vue project: composables only."""
    tree = parse(code, language)
    facts = FileFacts(path=path, language=language,
                      syntax_ok=not tree.root_node.has_error)
    facts.imports = extract_imports(tree.root_node)
    facts.hooks, _ = _extract_composables(tree.root_node, path, fetch_names)
    return facts


def _apply_composable_call(scope: ComponentFacts, call: HookCall,
                           hook: HookFacts | None,
                           owner: HookFacts | None) -> None:
    """Vue policy: map returns; a *ref* argument written by the
    composable's fetchy scope is promoted directly (Vue hands the ref
    itself in, there is no setter)."""
    if hook is None:
        return
    apply_hook_returns(scope, call, hook)
    for i, arg in enumerate(call.args):
        if not arg or i >= len(hook.scope.params):
            continue
        if hook.scope.params[i] not in hook.server_write_params:
            continue
        binding = scope.bindings.get(arg)
        if binding is None:
            continue
        if binding.origin == "local-state":
            binding.origin = "server-state"
        if owner is not None and arg in owner.scope.params:
            owner.server_write_params.add(arg)


_SCRIPT_LANGUAGES = ("typescript", "javascript")


def analyze_project(files: dict[str, str], language: str = "vue",
                    fetch_names: tuple[str, ...] = DEFAULT_FETCH_NAMES,
                    ) -> ProjectAnalysis:
    """Build the cross-file model of a Vue project: .vue SFCs become
    components, .ts/.js files contribute composables. Deterministic."""
    analysis = ProjectAnalysis(files={}, components={}, hooks={}, chains=[])
    for path in sorted(files):
        if path.endswith(".vue"):
            analysis.files[path] = extract_vue_file_facts(
                path, files[path], fetch_names=fetch_names)
        else:
            lang = language_for_path(path)
            if lang in _SCRIPT_LANGUAGES:
                analysis.files[path] = extract_composables_file(
                    path, files[path], lang, fetch_names=fetch_names)
    for path in sorted(analysis.files):
        for name, comp in analysis.files[path].components.items():
            analysis.components.setdefault(name, comp)
        for name, hook in analysis.files[path].hooks.items():
            analysis.hooks.setdefault(name, hook)
    resolve_hooks(analysis, _apply_composable_call,
                  forward_origins=_FORWARD_ORIGINS)
    resolve_components(analysis, _apply_composable_call)
    analysis.chains = build_chains(analysis)
    return analysis


def analyze_source(code: str, language: str = "vue",
                   fetch_names: tuple[str, ...] = DEFAULT_FETCH_NAMES,
                   ) -> ProjectAnalysis:
    """Analyze a single string, honoring `// @file:` virtual-file markers."""
    return analyze_project(split_virtual_files(code, "vue"),
                           language=language, fetch_names=fetch_names)


# ------------------------------------------------------- Vue-only checks


def check_vue_shared_mutable_state(analysis: ProjectAnalysis,
                                   params: dict[str, Any],
                                   ) -> list[SemanticFinding]:
    """A state ref mutated from below (v-model / @event assigning it) that
    also fans out to >= min_branches child branches — or whose value is
    drilled >= max_depth levels — is de-facto global mutable state and
    should move to provide/inject (or a Pinia store)."""
    min_branches = int(params.get("min_branches", 2))
    max_depth = int(params.get("max_depth", 3))
    findings = []
    for name in sorted(analysis.components):
        comp = analysis.components[name]
        edges = getattr(comp, "mutation_edges", [])
        if not edges:
            continue
        for value_name in sorted(comp.bindings):
            binding = comp.bindings[value_name]
            if binding.origin != "local-state":
                continue
            mutated = {
                child for child, ident in edges
                if ident == value_name
                or (ident in comp.bindings
                    and comp.bindings[ident].tracks(value_name))}
            if not mutated:
                continue

            def carries(p: PropPass) -> bool:
                return any(
                    s in comp.bindings and comp.bindings[s].tracks(value_name)
                    for s in p.sources)

            branches = {p.child for p in comp.passes
                        if not p.spread and carries(p)} | mutated
            depth = max(
                (c.depth for c in analysis.chains
                 if c.origin_component == name
                 and (c.source == value_name or c.origin_root == value_name)),
                default=0)
            wide = len(branches) >= min_branches
            deep = depth >= max_depth
            if not (wide or deep):
                continue
            reasons = [
                f"mutated from below by {', '.join(sorted(mutated))} "
                f"(v-model/@event)"]
            if wide:
                reasons.append(
                    f"fans out to {len(branches)} child branches "
                    f"({', '.join(sorted(branches))})")
            if deep:
                reasons.append(f"value drilled {depth} levels")
            findings.append(SemanticFinding(
                file=comp.file, line=binding.line, col=binding.col,
                detail=(
                    f"ref '{value_name}' in {name} is shared mutable state — "
                    + "; ".join(reasons)
                ),
            ))
    return findings


SEMANTIC_CHECKS.update({
    "vue-server-state-drilling": check_server_state_drilling,
    "vue-shared-mutable-state": check_vue_shared_mutable_state,
    "vue-prop-drilling": check_prop_drilling,
})
