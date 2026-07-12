"""React front-end for the cross-file data-flow analysis (see flow.py).

This module knows React's syntax and idioms — JSX render edges, hooks
(useState/useEffect/useQuery/useContext/useReducer), custom `use*` hooks,
`{...props}` spreads, `Context.Provider` — and extracts them into the
framework-neutral model in flow.py. The chain building, derived-value
provenance, hook fixpoint, and generic checkers all live there; the Vue
front-end (vue.py) shares them.

React-specific policy encoded here:
    server origin    a useState value whose *setter* is referenced inside a
                     useEffect that performs a fetch-like call (covers both
                     `setUser(x)` and `.then(setUser)`)
    setter handoff   a setter passed into a custom hook whose parameter is
                     fetch-written promotes the paired state
    mutation intent  check_shared_mutable_state: the value+setter pair
                     fanning out / the setter drilled deep -> Context
    Provider skip    values handed to `X.Provider` are not render edges —
                     that IS the sanctioned pattern
"""

from __future__ import annotations

from typing import Any

from tree_sitter import Node

from .flow import (
    Binding,
    ComponentFacts,
    CUSTOM_HOOK_NAME,
    DEFAULT_FETCH_NAMES,
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
    deepest_per_origin,
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
    run_semantic_check,
    semantic_check_names,
    split_virtual_files,
    text,
    top_level_functions,
    walk,
)
from .parser import language_for_path, parse

__all__ = [
    "REACT_LANGUAGES", "analyze_project", "analyze_source",
    "extract_file_facts", "run_semantic_check", "semantic_check_names",
    "split_virtual_files", "SEMANTIC_CHECKS",
]

REACT_LANGUAGES = ("javascript", "typescript", "tsx")

_HOOK_ORIGINS = {
    "useQuery": "query",
    "useSuspenseQuery": "query",
    "useInfiniteQuery": "query",
    "useSWR": "query",
    "useContext": "context",
}

# built-in hooks that are NOT treated as user-defined custom hooks
_BUILTIN_HOOKS = {
    "useState", "useReducer", "useEffect", "useLayoutEffect", "useMemo",
    "useCallback", "useRef", "useId", "useTransition", "useDeferredValue",
    "useImperativeHandle", "useSyncExternalStore", "useDebugValue",
} | set(_HOOK_ORIGINS)

_JSX_NODE_TYPES = ("jsx_element", "jsx_self_closing_element", "jsx_fragment")


def _contains_jsx(node: Node) -> bool:
    return any(n.type in _JSX_NODE_TYPES for n in walk(node))


# ------------------------------------------------------- fact extraction


def _extract_props(func: Node, comp: ComponentFacts) -> None:
    nodes = parameter_nodes(func)
    first = nodes[0] if nodes else None
    if first is None:
        return
    if first.type == "identifier":
        comp.props_param = text(first)
        return
    if first.type != "object_pattern":
        return
    for entry in first.named_children:
        if entry.type == "shorthand_property_identifier_pattern":
            comp.props.append(text(entry))
        elif entry.type == "pair_pattern":
            value = entry.child_by_field_name("value")
            if value is not None and value.type == "identifier":
                comp.props.append(text(value))
        elif entry.type == "rest_pattern":
            ident = next(
                (n for n in entry.named_children if n.type == "identifier"), None)
            if ident is not None:
                comp.rest_param = text(ident)
    for prop in comp.props:
        comp.bindings.setdefault(prop, Binding(
            prop, "prop", first.start_point[0] + 1, first.start_point[1],
            prop_roots=(prop,)))


def _extract_hook_params(func: Node, scope: ComponentFacts) -> None:
    names = [text(n) for n in parameter_nodes(func) if n.type == "identifier"]
    scope.params = tuple(names)
    for name in names:
        scope.bindings.setdefault(name, Binding(
            name, "local", func.start_point[0] + 1, func.start_point[1]))


def _scan_effect(effect_arg: Node, comp: ComponentFacts,
                 fetch_names: tuple[str, ...]) -> None:
    """Inside one useEffect callback: if a fetch-like call appears, record
    every referenced identifier (setters called or passed as `.then(setX)`
    callbacks, and hook parameters written through)."""
    has_fetch = False
    referenced: set[str] = set()
    for node in walk(effect_arg):
        if node.type == "call_expression":
            root = call_callee_root(node)
            if root in fetch_names:
                has_fetch = True
        elif node.type == "identifier":
            referenced.add(text(node))
    if not has_fetch:
        return
    comp.fetch_referenced |= referenced
    for binding in list(comp.bindings.values()):
        if binding.origin == "setter" and binding.name in referenced:
            state = comp.bindings.get(binding.partner or "")
            if state is not None and state.origin == "local-state":
                state.origin = "server-state"


def _extract_bindings(body: Node, comp: ComponentFacts,
                      fetch_names: tuple[str, ...]) -> None:
    """Declarations, hook calls, effects — everything provenance flows
    through inside one component/hook body."""
    effects: list[Node] = []
    for node in walk(body):
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None or callee.type != "identifier":
            continue
        name = text(callee)
        if name in ("useEffect", "useLayoutEffect"):
            args = node.child_by_field_name("arguments")
            if args is not None and args.named_children:
                effects.append(args.named_children[0])
            comp.hooks.add(name)
        elif CUSTOM_HOOK_NAME.match(name) and name not in _BUILTIN_HOOKS:
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

    for decl in walk(body):
        if decl.type != "variable_declarator":
            continue
        name_node = decl.child_by_field_name("name")
        value = decl.child_by_field_name("value")
        if name_node is None or value is None:
            continue
        line, col = name_node.start_point[0] + 1, name_node.start_point[1]

        callee = value.child_by_field_name("function") \
            if value.type == "call_expression" else None
        hook = text(callee) if callee is not None \
            and callee.type == "identifier" else ""

        if hook in ("useState", "useReducer") and name_node.type == "array_pattern":
            idents = [n for n in name_node.named_children if n.type == "identifier"]
            value_name = text(idents[0]) if idents else None
            setter_name = text(idents[1]) if len(idents) > 1 else None
            if value_name:
                comp.bindings[value_name] = Binding(
                    value_name, "local-state", line, col, partner=setter_name)
            if setter_name:
                setter_origin = "setter" if hook == "useState" else "dispatch"
                comp.bindings[setter_name] = Binding(
                    setter_name, setter_origin, line, col, partner=value_name)
            comp.hooks.add(hook)
            continue
        if hook in _HOOK_ORIGINS:
            origin = _HOOK_ORIGINS[hook]
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings[local] = Binding(local, origin, line, col)
            comp.hooks.add(hook)
            continue
        if hook and CUSTOM_HOOK_NAME.match(hook) and hook not in _BUILTIN_HOOKS:
            # placeholder bindings; overwritten when the hook resolves
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings.setdefault(local, Binding(local, "local", line, col))
            continue
        if hook in ("useMemo", "useCallback") or value.type not in (
                "identifier", "member_expression"):
            # a computed value: inherit provenance from its inputs
            deps = expr_candidates(value, comp)
            _, targets = pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings.setdefault(local, Binding(
                    local, "local", line, col, derived_from=deps))
            if hook in ("useMemo", "useCallback"):
                comp.hooks.add(hook)
            continue
        # plain alias / destructure of an identifier or member chain
        root, path = root_identifier(value)
        if root is None:
            continue
        if comp.props_param is not None and root == comp.props_param:
            # `const { user } = props` / `const u = props.user`
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
                root = path[0]
        _, targets = pattern_targets(name_node)
        for _key, local in targets:
            comp.bindings.setdefault(local, Binding(
                local, "local", line, col, derived_from=(root,)))

    for effect in effects:
        _scan_effect(effect, comp, fetch_names)


def _jsx_pass_sources(comp: ComponentFacts, expr: Node,
                      ) -> tuple[tuple[str, ...], bool]:
    """(candidate identifiers, derived?) for one JSX attribute value."""
    root, path = root_identifier(expr)
    if root is not None:
        if comp.props_param is not None and root == comp.props_param:
            if not path:
                return (), False  # whole props object as one value
            note_prop(comp, path[0], expr)
            return (path[0],), False
        return (root,), False
    candidates = expr_candidates(expr, comp)
    return candidates, True


def _extract_passes(body: Node, comp: ComponentFacts) -> None:
    for node in walk(body):
        if node.type not in ("jsx_self_closing_element", "jsx_opening_element"):
            continue
        name_node = node.child_by_field_name("name") or next(
            (n for n in node.named_children if n.type == "identifier"), None)
        if name_node is None:
            continue
        child_name = text(name_node)
        if not child_name[:1].isupper():
            continue  # host elements (<div>) end a chain naturally
        if child_name == "Provider" or child_name.endswith(".Provider"):
            continue  # handing state to a Provider IS the sanctioned pattern
        for attr in node.named_children:
            if attr.type == "jsx_attribute":
                key = next((n for n in attr.named_children
                            if n.type == "property_identifier"), None)
                expr_wrap = next((n for n in attr.named_children
                                  if n.type == "jsx_expression"), None)
                if key is None or expr_wrap is None or not expr_wrap.named_children:
                    continue
                sources, derived = _jsx_pass_sources(
                    comp, expr_wrap.named_children[0])
                if not sources:
                    continue
                comp.passes.append(PropPass(
                    child=child_name, attr=text(key), sources=sources,
                    line=attr.start_point[0] + 1, col=attr.start_point[1],
                    derived=derived))
            elif attr.type == "jsx_expression":
                spread = next((n for n in attr.named_children
                               if n.type == "spread_element"), None)
                if spread is None:
                    continue
                ident = next((n for n in spread.named_children
                              if n.type == "identifier"), None)
                if ident is None:
                    continue
                name = text(ident)
                if name in (comp.props_param, comp.rest_param):
                    comp.passes.append(PropPass(
                        child=child_name, attr="*", sources=(name,),
                        line=attr.start_point[0] + 1, col=attr.start_point[1],
                        spread=True))


def extract_file_facts(path: str, code: str, language: str | None = None,
                       fetch_names: tuple[str, ...] = DEFAULT_FETCH_NAMES,
                       ) -> FileFacts:
    lang = language or language_for_path(path) or "tsx"
    if lang not in REACT_LANGUAGES:
        lang = "tsx"
    tree = parse(code, lang)
    facts = FileFacts(path=path, language=lang,
                      syntax_ok=not tree.root_node.has_error)
    facts.imports = extract_imports(tree.root_node)
    for name, name_node, func in top_level_functions(tree.root_node):
        body = function_body(func) or func
        line, col = name_node.start_point[0] + 1, name_node.start_point[1]
        if CUSTOM_HOOK_NAME.match(name):
            scope = ComponentFacts(name=name, file=path, line=line, col=col)
            _extract_hook_params(func, scope)
            _extract_bindings(body, scope, fetch_names)
            scope.returns_spec = extract_returns_spec(func, body)
            facts.hooks[name] = HookFacts(
                name=name, file=path, line=line, scope=scope)
        elif name[:1].isupper() and _contains_jsx(body):
            comp = ComponentFacts(name=name, file=path, line=line, col=col)
            _extract_props(func, comp)
            _extract_bindings(body, comp, fetch_names)
            _extract_passes(body, comp)
            facts.components[name] = comp
    return facts


# --------------------------------------------------------------- analysis


def _apply_hook_call(scope: ComponentFacts, call: HookCall,
                     hook: HookFacts | None,
                     owner: HookFacts | None) -> None:
    """React policy: map returns; a *setter* argument written by the hook's
    fetch effect promotes its paired state."""
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
        if binding.origin == "setter" and binding.partner:
            state = scope.bindings.get(binding.partner)
            if state is not None and state.origin == "local-state":
                state.origin = "server-state"
        if owner is not None and arg in owner.scope.params:
            owner.server_write_params.add(arg)


def analyze_project(files: dict[str, str], language: str = "tsx",
                    fetch_names: tuple[str, ...] = DEFAULT_FETCH_NAMES,
                    ) -> ProjectAnalysis:
    """Build the full cross-file model. Deterministic for a given input."""
    analysis = ProjectAnalysis(files={}, components={}, hooks={}, chains=[])
    for path in sorted(files):
        analysis.files[path] = extract_file_facts(
            path, files[path],
            language=None if language_for_path(path) else language,
            fetch_names=fetch_names)
    for path in sorted(analysis.files):
        for name, comp in analysis.files[path].components.items():
            analysis.components.setdefault(name, comp)
        for name, hook in analysis.files[path].hooks.items():
            analysis.hooks.setdefault(name, hook)
    resolve_hooks(analysis, _apply_hook_call, forward_origins=_HOOK_ORIGINS)
    resolve_components(analysis, _apply_hook_call)
    analysis.chains = build_chains(analysis)
    return analysis


def analyze_source(code: str, language: str = "tsx",
                   fetch_names: tuple[str, ...] = DEFAULT_FETCH_NAMES,
                   ) -> ProjectAnalysis:
    """Analyze a single string, honoring `// @file:` virtual-file markers."""
    return analyze_project(split_virtual_files(code, language),
                           language=language, fetch_names=fetch_names)


# ----------------------------------------------------- React-only checks


def check_shared_mutable_state(analysis: ProjectAnalysis,
                               params: dict[str, Any]) -> list[SemanticFinding]:
    """A useState pair whose setter is handed down and whose value/setter
    fan out to >= min_branches child subtrees — or whose setter alone is
    drilled >= max_setter_depth — is de-facto global mutable state and
    should move to Context (or a store)."""
    min_branches = int(params.get("min_branches", 2))
    max_setter_depth = int(params.get("max_setter_depth", 3))
    findings = []
    for name in sorted(analysis.components):
        comp = analysis.components[name]
        for value_name in sorted(comp.bindings):
            binding = comp.bindings[value_name]
            if binding.origin != "local-state" or not binding.partner:
                continue
            setter = binding.partner

            def carries(p: PropPass, target: str) -> bool:
                return any(
                    s in comp.bindings and comp.bindings[s].tracks(target)
                    for s in p.sources)

            branches = {p.child for p in comp.passes if not p.spread
                        and (carries(p, value_name) or carries(p, setter))}
            setter_passed = any(not p.spread and carries(p, setter)
                                for p in comp.passes)
            setter_chains = [
                c for c in analysis.chains
                if c.origin_component == name
                and (c.source == setter or c.origin_root == setter)]
            setter_depth = max((c.depth for c in setter_chains), default=0)
            wide = setter_passed and len(branches) >= min_branches
            deep = setter_depth >= max_setter_depth
            if not (wide or deep):
                continue
            reasons = []
            if wide:
                reasons.append(
                    f"value+setter fan out to {len(branches)} child branches "
                    f"({', '.join(sorted(branches))})")
            if deep:
                deepest = deepest_per_origin(setter_chains)
                reasons.append(
                    f"setter '{setter}' drilled {setter_depth} levels"
                    + (f": {deepest[0].path()}" if deepest else ""))
            findings.append(SemanticFinding(
                file=comp.file, line=binding.line, col=binding.col,
                detail=(
                    f"state '{value_name}' in {name} is mutated from below "
                    f"and shared — " + "; ".join(reasons)
                ),
            ))
    return findings


SEMANTIC_CHECKS.update({
    "react-server-state-drilling": check_server_state_drilling,
    "react-shared-mutable-state": check_shared_mutable_state,
    "react-prop-drilling": check_prop_drilling,
})
