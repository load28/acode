"""Cross-file React semantic analysis.

Single-file tree-sitter queries cannot express conventions that depend on
*several places at once* — e.g. "a prop drilled 3+ levels whose value came
from a fetch-in-effect must move to React Query". This module builds a
deterministic, LLM-free model of a React project and runs registered
semantic checks over it:

    per-file facts   components (capitalized functions rendering JSX),
                     custom hooks (use* functions), received props, hook
                     bindings (useState / useEffect / useQuery /
                     useContext / useReducer), and render edges
                     (`<Child data={x} />`)
    provenance       every value passed as a JSX attribute is classified:
                     server-state (useState fed by fetch/axios inside an
                     effect), local-state, setter, dispatch, query
                     (React Query/SWR), context, local, or prop passthrough
    derived values   `const rows = transform(data)` inherits `data`'s
                     origin; inline transforms (`items={merge(a, b)}`) and
                     callback wrappers (`onChange={(v) => setFilter(v)}`)
                     keep the chain alive, marked as derived (`~[prop]~>`)
    custom hooks     `useUser()` wrapping useState + fetch-in-effect is
                     analyzed: what it returns carries the internal
                     origins to the call site. Hooks calling hooks resolve
                     via a bounded fixpoint; a setter passed INTO a hook
                     whose effect fetches promotes the paired state.
    prop chains      DFS across the resolved component graph following
                     passthrough edges — depth = how many component
                     boundaries the value crosses

Determinism: the same set of files always yields the same findings in the
same order. Values handed to a `Something.Provider` are deliberately NOT
counted — that is the sanctioned Context pattern, not drilling.

Multi-file projects can travel as a single string using `// @file:` marker
lines, so semantic rules keep working through the existing single-string
entry points (check_code, store self-verification of examples).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from tree_sitter import Node

from .parser import language_for_path, parse

REACT_LANGUAGES = ("javascript", "typescript", "tsx")

_FILE_MARKER = re.compile(r"^[ \t]*//[ \t]*@file:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

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

_CUSTOM_HOOK_NAME = re.compile(r"^use[A-Z0-9_]")

_DEFAULT_FETCH_NAMES = ("fetch", "axios")

_JSX_NODE_TYPES = ("jsx_element", "jsx_self_closing_element", "jsx_fragment")

_FUNCTION_NODE_TYPES = ("arrow_function", "function_expression",
                        "function_declaration", "function")

_DEFAULT_VIRTUAL_FILE = {
    "javascript": "Main.jsx",
    "typescript": "Main.ts",
    "tsx": "Main.tsx",
}

# strongest first: a derived value inherits its strongest source's origin
_ORIGIN_PRIORITY = ("server-state", "query", "context", "local-state",
                    "dispatch", "setter", "prop", "local")


def _priority(origin: str) -> int:
    try:
        return _ORIGIN_PRIORITY.index(origin)
    except ValueError:
        return len(_ORIGIN_PRIORITY)


def split_virtual_files(code: str, language: str = "tsx") -> dict[str, str]:
    """Split a single string into virtual files on `// @file: path` markers.

    Without markers the whole string is one file (named by language). Text
    before the first marker, if any, also lands in that default file.
    """
    default_name = _DEFAULT_VIRTUAL_FILE.get(language, "Main.tsx")
    matches = list(_FILE_MARKER.finditer(code))
    if not matches:
        return {default_name: code}
    files: dict[str, str] = {}
    head = code[: matches[0].start()]
    if head.strip():
        files[default_name] = head
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        files[match.group(1)] = code[match.end():end].lstrip("\n")
    return files


# ------------------------------------------------------------------ model


@dataclass
class Binding:
    """Provenance of an identifier inside a component or hook."""

    name: str
    origin: str  # server-state | local-state | setter | dispatch | query
    #            # | context | local | prop
    line: int
    col: int
    partner: str | None = None  # setter <-> state value
    derived_from: tuple[str, ...] = ()  # direct identifier deps, if derived
    prop_roots: tuple[str, ...] = ()  # received props this value stems from
    origin_root: str | None = None  # underlying identifier, for messages

    def tracks(self, name: str) -> bool:
        """Is this binding `name` itself or derived from it?"""
        return self.name == name or self.origin_root == name


@dataclass
class PropPass:
    """One JSX attribute edge: value(s) leaving a component into a child."""

    child: str
    attr: str  # '*' for a spread
    sources: tuple[str, ...]  # candidate local identifiers rooted in value
    line: int
    col: int
    spread: bool = False
    derived: bool = False  # value was transformed, not passed verbatim

    @property
    def source(self) -> str:
        return self.sources[0] if self.sources else ""


@dataclass
class HookCall:
    """A call to a user-defined hook inside a component or another hook."""

    hook: str
    args: tuple[str, ...]  # root identifier per argument ('' if complex)
    target_kind: str | None  # identifier | object | array | None
    target_names: tuple[tuple[str, str], ...]  # (key_or_index, local_name)
    line: int
    col: int


@dataclass
class ComponentFacts:
    name: str
    file: str
    line: int
    col: int
    props: list[str] = field(default_factory=list)  # destructured names
    props_param: str | None = None  # `(props)` style parameter
    rest_param: str | None = None  # `{a, ...rest}` rest name
    params: tuple[str, ...] = ()  # plain parameters (hooks use these)
    bindings: dict[str, Binding] = field(default_factory=dict)
    passes: list[PropPass] = field(default_factory=list)
    hooks: set[str] = field(default_factory=set)
    hook_calls: list[HookCall] = field(default_factory=list)
    fetch_referenced: set[str] = field(default_factory=set)
    returns_spec: tuple = ()  # last own return: ('object'|... , payload)

    def receives(self, prop: str) -> bool:
        return prop in self.props or self.props_param is not None


@dataclass
class HookFacts:
    """A user-defined hook; bindings live in `scope` (same machinery as
    components, minus JSX render edges)."""

    name: str
    file: str
    line: int
    scope: ComponentFacts
    server_write_params: set[str] = field(default_factory=set)
    returns: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileFacts:
    path: str
    language: str
    syntax_ok: bool
    components: dict[str, ComponentFacts] = field(default_factory=dict)
    hooks: dict[str, HookFacts] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)  # local -> module


@dataclass
class ChainHop:
    component: str
    prop: str
    line: int
    derived: bool = False  # the value was transformed at this hop


@dataclass
class PropChain:
    origin_component: str
    origin_file: str
    source: str  # identifier at the origin
    origin: str  # binding origin kind
    line: int
    col: int
    hops: list[ChainHop]
    origin_root: str | None = None  # if the origin identifier is derived

    @property
    def depth(self) -> int:
        return len(self.hops)

    def path(self) -> str:
        parts = [self.origin_component]
        for hop in self.hops:
            arrow = f"~[{hop.prop}]~>" if hop.derived else f"-[{hop.prop}]->"
            parts.append(f"{arrow} {hop.component}")
        return " ".join(parts)

    def describe_source(self) -> str:
        if self.origin_root and self.origin_root != self.source:
            return f"'{self.source}' (derived from '{self.origin_root}')"
        return f"'{self.source}'"


@dataclass
class ProjectAnalysis:
    files: dict[str, FileFacts]
    components: dict[str, ComponentFacts]
    hooks: dict[str, HookFacts]
    chains: list[PropChain]

    @property
    def syntax_ok(self) -> bool:
        return all(f.syntax_ok for f in self.files.values())


@dataclass
class SemanticFinding:
    file: str
    line: int
    col: int
    detail: str
    snippet: str = ""


# ------------------------------------------------------- fact extraction


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _contains_jsx(node: Node) -> bool:
    return any(n.type in _JSX_NODE_TYPES for n in _walk(node))


def _root_identifier(node: Node) -> tuple[str | None, list[str]]:
    """Root identifier of an expression plus the member path below it.

    `user` -> ('user', []); `props.user.name` -> ('props', ['user', 'name']).
    Anything else (calls, literals, ternaries) returns (None, []) — those go
    through candidate collection (`_expr_candidates`) as derived values.
    """
    path: list[str] = []
    while node.type in ("member_expression", "subscript_expression"):
        if node.type == "member_expression":
            prop = node.child_by_field_name("property")
            if prop is not None:
                path.insert(0, _text(prop))
        obj = node.child_by_field_name("object")
        if obj is None:
            return None, []
        node = obj
    if node.type == "identifier":
        return _text(node), path
    return None, []


def _expr_candidates(expr: Node, comp: ComponentFacts) -> tuple[str, ...]:
    """Identifiers a transformed expression is built from, in document
    order. Called identifiers count too — `(v) => setFilter(v)` hands the
    setter down in a wrapper (unbound helpers like `transform` simply never
    match a binding later). Excluded: parameters of nested arrows, JSX
    subtrees (they become their own render edges), and member property
    names. A member access rooted at the props object (`props.user`)
    contributes the prop name."""
    out: list[str] = []

    def visit(node: Node) -> None:
        if node.type in _JSX_NODE_TYPES:
            return
        if node.type == "member_expression":
            root, path = _root_identifier(node)
            if root is not None:
                if comp.props_param is not None and root == comp.props_param:
                    if path:
                        _note_prop(comp, path[0], node)
                        out.append(path[0])
                else:
                    out.append(root)
                return  # a resolvable chain: don't double-count its root
            # chain rooted in a call (`data.filter(x).map(y)`): descend
        elif node.type == "identifier":
            parent = node.parent
            if parent is not None and parent.type in _FUNCTION_NODE_TYPES \
                    and parent.child_by_field_name("body") != node:
                return  # arrow parameter
            name = _text(node)
            if name != comp.props_param:
                out.append(name)
            return
        elif node.type == "formal_parameters":
            return
        for child in node.named_children:
            visit(child)

    visit(expr)
    seen: set[str] = set()
    unique = []
    for name in out:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return tuple(unique)


def _note_prop(comp: ComponentFacts, prop: str, node: Node) -> None:
    comp.bindings.setdefault(prop, Binding(
        prop, "prop", node.start_point[0] + 1, node.start_point[1],
        prop_roots=(prop,)))
    if prop not in comp.props:
        comp.props.append(prop)


def _function_body(func: Node) -> Node | None:
    return func.child_by_field_name("body")


def _unwrap_parameter(node: Node) -> Node:
    """tsx wraps parameters in required_parameter/optional_parameter."""
    if node.type in ("required_parameter", "optional_parameter"):
        for child in node.named_children:
            if child.type in ("object_pattern", "identifier", "array_pattern"):
                return child
    return node


def _parameter_nodes(func: Node) -> list[Node]:
    params = func.child_by_field_name("parameters")
    if params is not None:
        return [_unwrap_parameter(n) for n in params.named_children
                if n.type != "comment"]
    single = func.child_by_field_name("parameter")
    return [_unwrap_parameter(single)] if single is not None else []


def _extract_props(func: Node, comp: ComponentFacts) -> None:
    nodes = _parameter_nodes(func)
    first = nodes[0] if nodes else None
    if first is None:
        return
    if first.type == "identifier":
        comp.props_param = _text(first)
        return
    if first.type != "object_pattern":
        return
    for entry in first.named_children:
        if entry.type == "shorthand_property_identifier_pattern":
            comp.props.append(_text(entry))
        elif entry.type == "pair_pattern":
            value = entry.child_by_field_name("value")
            if value is not None and value.type == "identifier":
                comp.props.append(_text(value))
        elif entry.type == "rest_pattern":
            ident = next(
                (n for n in entry.named_children if n.type == "identifier"), None)
            if ident is not None:
                comp.rest_param = _text(ident)
    for prop in comp.props:
        comp.bindings.setdefault(prop, Binding(
            prop, "prop", first.start_point[0] + 1, first.start_point[1],
            prop_roots=(prop,)))


def _extract_hook_params(func: Node, scope: ComponentFacts) -> None:
    names = [_text(n) for n in _parameter_nodes(func) if n.type == "identifier"]
    scope.params = tuple(names)
    for name in names:
        scope.bindings.setdefault(name, Binding(
            name, "local", func.start_point[0] + 1, func.start_point[1]))


def _call_callee_root(call: Node) -> str | None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    root, _ = _root_identifier(fn)
    return root


def _scan_effect(effect_arg: Node, comp: ComponentFacts,
                 fetch_names: tuple[str, ...]) -> None:
    """Inside one useEffect callback: if a fetch-like call appears, record
    every referenced identifier (setters called or passed as `.then(setX)`
    callbacks, and hook parameters written through)."""
    has_fetch = False
    referenced: set[str] = set()
    for node in _walk(effect_arg):
        if node.type == "call_expression":
            root = _call_callee_root(node)
            if root in fetch_names:
                has_fetch = True
        elif node.type == "identifier":
            referenced.add(_text(node))
    if not has_fetch:
        return
    comp.fetch_referenced |= referenced
    for binding in list(comp.bindings.values()):
        if binding.origin == "setter" and binding.name in referenced:
            state = comp.bindings.get(binding.partner or "")
            if state is not None and state.origin == "local-state":
                state.origin = "server-state"


def _pattern_targets(name_node: Node) -> tuple[str | None, tuple[tuple[str, str], ...]]:
    """(kind, ((key_or_index, local_name), ...)) for a declarator LHS."""
    if name_node.type == "identifier":
        return "identifier", (("", _text(name_node)),)
    if name_node.type == "object_pattern":
        entries: list[tuple[str, str]] = []
        for entry in name_node.named_children:
            if entry.type == "shorthand_property_identifier_pattern":
                entries.append((_text(entry), _text(entry)))
            elif entry.type == "pair_pattern":
                key = entry.child_by_field_name("key")
                value = entry.child_by_field_name("value")
                if key is not None and value is not None \
                        and value.type == "identifier":
                    entries.append((_text(key), _text(value)))
        return "object", tuple(entries)
    if name_node.type == "array_pattern":
        idents = [n for n in name_node.named_children if n.type == "identifier"]
        return "array", tuple((str(i), _text(n)) for i, n in enumerate(idents))
    return None, ()


def _extract_returns_spec(func: Node, body: Node) -> tuple:
    """Shape of the hook's own (non-nested) return value."""
    def own_returns(node: Node) -> Iterator[Node]:
        for child in node.named_children:
            if child.type in _FUNCTION_NODE_TYPES:
                continue
            if child.type == "return_statement":
                yield child
            else:
                yield from own_returns(child)

    expr: Node | None = None
    if body.type != "statement_block":
        expr = body  # arrow with expression body
    else:
        for ret in own_returns(body):
            candidates = [n for n in ret.named_children
                          if n.type not in ("comment",)]
            if candidates:
                expr = candidates[0]
    if expr is None:
        return ("none",)
    if expr.type == "parenthesized_expression" and expr.named_children:
        expr = expr.named_children[0]
    if expr.type == "object":
        entries: list[tuple[str, str]] = []
        for entry in expr.named_children:
            if entry.type == "shorthand_property_identifier":
                entries.append((_text(entry), _text(entry)))
            elif entry.type == "pair":
                key = entry.child_by_field_name("key")
                value = entry.child_by_field_name("value")
                if key is not None and value is not None \
                        and value.type == "identifier":
                    entries.append((_text(key), _text(value)))
        return ("object", tuple(entries))
    if expr.type == "identifier":
        return ("identifier", _text(expr))
    if expr.type == "array":
        return ("array", tuple(
            _text(n) if n.type == "identifier" else ""
            for n in expr.named_children))
    if expr.type == "call_expression":
        callee = _call_callee_root(expr)
        if callee:
            return ("call", callee)
    return ("none",)


def _extract_bindings(body: Node, comp: ComponentFacts,
                      fetch_names: tuple[str, ...]) -> None:
    """Declarations, hook calls, effects — everything provenance flows
    through inside one component/hook body."""
    effects: list[Node] = []
    for node in _walk(body):
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None or callee.type != "identifier":
            continue
        name = _text(callee)
        if name in ("useEffect", "useLayoutEffect"):
            args = node.child_by_field_name("arguments")
            if args is not None and args.named_children:
                effects.append(args.named_children[0])
            comp.hooks.add(name)
        elif _CUSTOM_HOOK_NAME.match(name) and name not in _BUILTIN_HOOKS:
            args_node = node.child_by_field_name("arguments")
            arg_roots = tuple(
                _root_identifier(a)[0] or ""
                for a in (args_node.named_children if args_node is not None else []))
            target_kind, target_names = None, ()
            parent = node.parent
            if parent is not None and parent.type == "variable_declarator" \
                    and parent.child_by_field_name("value") == node:
                name_node = parent.child_by_field_name("name")
                if name_node is not None:
                    target_kind, target_names = _pattern_targets(name_node)
            comp.hook_calls.append(HookCall(
                hook=name, args=arg_roots,
                target_kind=target_kind, target_names=target_names,
                line=node.start_point[0] + 1, col=node.start_point[1]))
            comp.hooks.add(name)

    for decl in _walk(body):
        if decl.type != "variable_declarator":
            continue
        name_node = decl.child_by_field_name("name")
        value = decl.child_by_field_name("value")
        if name_node is None or value is None:
            continue
        line, col = name_node.start_point[0] + 1, name_node.start_point[1]

        callee = value.child_by_field_name("function") \
            if value.type == "call_expression" else None
        hook = _text(callee) if callee is not None \
            and callee.type == "identifier" else ""

        if hook in ("useState", "useReducer") and name_node.type == "array_pattern":
            idents = [n for n in name_node.named_children if n.type == "identifier"]
            value_name = _text(idents[0]) if idents else None
            setter_name = _text(idents[1]) if len(idents) > 1 else None
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
            _, targets = _pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings[local] = Binding(local, origin, line, col)
            comp.hooks.add(hook)
            continue
        if hook and _CUSTOM_HOOK_NAME.match(hook) and hook not in _BUILTIN_HOOKS:
            # placeholder bindings; overwritten when the hook resolves
            _, targets = _pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings.setdefault(local, Binding(local, "local", line, col))
            continue
        if hook in ("useMemo", "useCallback") or value.type not in (
                "identifier", "member_expression"):
            # a computed value: inherit provenance from its inputs
            deps = _expr_candidates(value, comp)
            _, targets = _pattern_targets(name_node)
            for _key, local in targets:
                comp.bindings.setdefault(local, Binding(
                    local, "local", line, col, derived_from=deps))
            if hook in ("useMemo", "useCallback"):
                comp.hooks.add(hook)
            continue
        # plain alias / destructure of an identifier or member chain
        root, path = _root_identifier(value)
        if root is None:
            continue
        if comp.props_param is not None and root == comp.props_param:
            # `const { user } = props` / `const u = props.user`
            if name_node.type == "object_pattern" and not path:
                _, targets = _pattern_targets(name_node)
                for key, local in targets:
                    _note_prop(comp, key, name_node)
                    if local != key:
                        comp.bindings[local] = Binding(
                            local, "prop", line, col, prop_roots=(key,))
                continue
            if path:
                _note_prop(comp, path[0], value)
                root = path[0]
        _, targets = _pattern_targets(name_node)
        for _key, local in targets:
            comp.bindings.setdefault(local, Binding(
                local, "local", line, col, derived_from=(root,)))

    for effect in effects:
        _scan_effect(effect, comp, fetch_names)


def _resolve_derived(bindings: dict[str, Binding]) -> None:
    """Fixpoint: derived bindings inherit origin (strongest source wins)
    and accumulate the received-prop roots they stem from."""
    names = sorted(bindings)
    for _ in range(len(names) + 1):
        changed = False
        for name in names:
            binding = bindings[name]
            if not binding.derived_from:
                continue
            deps = [bindings[d] for d in binding.derived_from
                    if d in bindings and d != name]
            if not deps:
                continue
            best = min(deps, key=lambda d: (_priority(d.origin), d.name))
            new_origin = best.origin if _priority(best.origin) < _priority("local") \
                else "local"
            new_roots = tuple(sorted({r for d in deps for r in d.prop_roots}))
            new_root = binding.origin_root
            if new_origin != "local":
                new_root = best.origin_root or best.name
            state = (binding.origin, binding.prop_roots, binding.origin_root)
            if state != (new_origin, new_roots, new_root):
                binding.origin = new_origin
                binding.prop_roots = new_roots
                binding.origin_root = new_root
                changed = True
        if not changed:
            return


def _jsx_pass_sources(comp: ComponentFacts, expr: Node,
                      ) -> tuple[tuple[str, ...], bool]:
    """(candidate identifiers, derived?) for one JSX attribute value."""
    root, path = _root_identifier(expr)
    if root is not None:
        if comp.props_param is not None and root == comp.props_param:
            if not path:
                return (), False  # whole props object as one value
            _note_prop(comp, path[0], expr)
            return (path[0],), False
        return (root,), False
    candidates = _expr_candidates(expr, comp)
    return candidates, True


def _extract_passes(body: Node, comp: ComponentFacts) -> None:
    for node in _walk(body):
        if node.type not in ("jsx_self_closing_element", "jsx_opening_element"):
            continue
        name_node = node.child_by_field_name("name") or next(
            (n for n in node.named_children if n.type == "identifier"), None)
        if name_node is None:
            continue
        child_name = _text(name_node)
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
                    child=child_name, attr=_text(key), sources=sources,
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
                name = _text(ident)
                if name in (comp.props_param, comp.rest_param):
                    comp.passes.append(PropPass(
                        child=child_name, attr="*", sources=(name,),
                        line=attr.start_point[0] + 1, col=attr.start_point[1],
                        spread=True))


def _top_level_functions(root: Node) -> Iterator[tuple[str, Node, Node]]:
    """Yield (name, name_node, function_node) for top-level functions."""
    for node in root.named_children:
        target = node
        if node.type == "export_statement":
            decl = node.child_by_field_name("declaration")
            if decl is None:
                continue
            target = decl
        if target.type == "function_declaration":
            name_node = target.child_by_field_name("name")
            if name_node is not None:
                yield _text(name_node), name_node, target
        elif target.type == "lexical_declaration":
            for decl in target.named_children:
                if decl.type != "variable_declarator":
                    continue
                name_node = decl.child_by_field_name("name")
                value = decl.child_by_field_name("value")
                if name_node is None or value is None:
                    continue
                if name_node.type == "identifier" and value.type in (
                        "arrow_function", "function_expression", "function"):
                    yield _text(name_node), name_node, value


def _extract_imports(root: Node) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in root.named_children:
        if node.type != "import_statement":
            continue
        source = node.child_by_field_name("source")
        module = ""
        if source is not None:
            frag = next((n for n in source.named_children
                         if n.type == "string_fragment"), None)
            module = _text(frag) if frag is not None else _text(source).strip("'\"")
        for n in _walk(node):
            if n.type == "import_specifier":
                alias = n.child_by_field_name("alias")
                name = alias if alias is not None else n.child_by_field_name("name")
                if name is not None:
                    imports[_text(name)] = module
            elif n.type == "identifier" and n.parent is not None \
                    and n.parent.type == "import_clause":
                imports[_text(n)] = module  # default import
    return imports


def extract_file_facts(path: str, code: str, language: str | None = None,
                       fetch_names: tuple[str, ...] = _DEFAULT_FETCH_NAMES,
                       ) -> FileFacts:
    lang = language or language_for_path(path) or "tsx"
    if lang not in REACT_LANGUAGES:
        lang = "tsx"
    tree = parse(code, lang)
    facts = FileFacts(path=path, language=lang,
                      syntax_ok=not tree.root_node.has_error)
    facts.imports = _extract_imports(tree.root_node)
    for name, name_node, func in _top_level_functions(tree.root_node):
        body = _function_body(func) or func
        line, col = name_node.start_point[0] + 1, name_node.start_point[1]
        if _CUSTOM_HOOK_NAME.match(name):
            scope = ComponentFacts(name=name, file=path, line=line, col=col)
            _extract_hook_params(func, scope)
            _extract_bindings(body, scope, fetch_names)
            scope.returns_spec = _extract_returns_spec(func, body)
            facts.hooks[name] = HookFacts(
                name=name, file=path, line=line, scope=scope)
        elif name[:1].isupper() and _contains_jsx(body):
            comp = ComponentFacts(name=name, file=path, line=line, col=col)
            _extract_props(func, comp)
            _extract_bindings(body, comp, fetch_names)
            _extract_passes(body, comp)
            facts.components[name] = comp
    return facts


# ------------------------------------------------- custom hook resolution


def _resolve_named(analysis: ProjectAnalysis, from_file: str, name: str,
                   registry_attr: str) -> Any | None:
    """Same-file first, then the import's module basename, then a global
    (sorted-first) name match. Works for components and hooks alike."""
    file_facts = analysis.files.get(from_file)
    if file_facts is not None:
        local = getattr(file_facts, registry_attr).get(name)
        if local is not None:
            return local
        module = file_facts.imports.get(name)
        if module:
            stem = Path(module).name
            for path in sorted(analysis.files):
                if Path(path).stem == stem:
                    found = getattr(analysis.files[path], registry_attr).get(name)
                    if found is not None:
                        return found
    registry = getattr(analysis, registry_attr)
    return registry.get(name)


def _compute_returns(hook: HookFacts) -> dict[str, Any]:
    spec = hook.scope.returns_spec
    bindings = hook.scope.bindings

    def describe(ident: str) -> dict[str, Any]:
        binding = bindings.get(ident)
        if binding is None:
            return {"origin": "local", "partner": None}
        return {"origin": binding.origin, "partner": binding.partner}

    if not spec or spec[0] == "none":
        return {"kind": "none"}
    if spec[0] == "identifier":
        info = describe(spec[1])
        return {"kind": "single", "origin": info["origin"]}
    if spec[0] == "object":
        entries: dict[str, dict[str, Any]] = {}
        ident_to_key = {ident: key for key, ident in spec[1]}
        for key, ident in spec[1]:
            info = describe(ident)
            entries[key] = {
                "origin": info["origin"],
                "partner_key": ident_to_key.get(info["partner"] or ""),
            }
        return {"kind": "object", "entries": entries}
    if spec[0] == "array":
        idents = list(spec[1])
        items = []
        for ident in idents:
            info = describe(ident)
            partner_idx = None
            if info["partner"] in idents:
                partner_idx = idents.index(info["partner"])
            items.append({"origin": info["origin"], "partner_index": partner_idx})
        return {"kind": "array", "items": items}
    if spec[0] == "call":
        callee = spec[1]
        if callee in _HOOK_ORIGINS:
            return {"kind": "single", "origin": _HOOK_ORIGINS[callee]}
        return {"kind": "forward", "hook": callee}
    return {"kind": "none"}


def _best_of(origins: list[str]) -> str:
    if not origins:
        return "local"
    return min(origins, key=_priority)


def _apply_hook_call(scope: ComponentFacts, call: HookCall,
                     hook: HookFacts | None,
                     owner: HookFacts | None) -> None:
    """Push a resolved hook's return origins into the caller's bindings and
    promote setters handed in as arguments the hook fetch-writes."""
    if hook is None:
        return
    returns = hook.returns

    if call.target_kind and returns:
        kind = returns.get("kind")
        if kind == "object" and call.target_kind == "object":
            entries = returns.get("entries", {})
            key_to_local = dict(call.target_names)
            for key, local in call.target_names:
                info = entries.get(key)
                if info is None:
                    continue
                partner_local = key_to_local.get(info.get("partner_key") or "")
                scope.bindings[local] = Binding(
                    local, info["origin"], call.line, call.col,
                    partner=partner_local)
        elif kind == "array" and call.target_kind == "array":
            items = returns.get("items", [])
            idx_to_local = dict(call.target_names)
            for idx_str, local in call.target_names:
                idx = int(idx_str)
                if idx >= len(items):
                    continue
                info = items[idx]
                partner_local = None
                if info.get("partner_index") is not None:
                    partner_local = idx_to_local.get(str(info["partner_index"]))
                scope.bindings[local] = Binding(
                    local, info["origin"], call.line, call.col,
                    partner=partner_local)
        elif call.target_kind == "identifier" and call.target_names:
            local = call.target_names[0][1]
            if kind == "single":
                origin = returns.get("origin", "local")
            elif kind == "object":
                origin = _best_of([e["origin"]
                                   for e in returns.get("entries", {}).values()])
            elif kind == "array":
                origin = _best_of([i["origin"] for i in returns.get("items", [])])
            else:
                origin = "local"
            scope.bindings[local] = Binding(local, origin, call.line, call.col)

    # a setter argument written by the hook's fetch effect makes the paired
    # state server state; a parameter forwarded hook-to-hook propagates
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


def _snapshot(hook: HookFacts) -> tuple:
    return (
        tuple(sorted((b.name, b.origin, b.partner or "")
                     for b in hook.scope.bindings.values())),
        tuple(sorted(hook.server_write_params)),
        repr(hook.returns),
    )


def _resolve_hooks(analysis: ProjectAnalysis) -> None:
    """Bounded fixpoint over user-defined hooks (they may call each other,
    in any file order)."""
    hooks = [analysis.hooks[name] for name in sorted(analysis.hooks)]
    for _ in range(len(hooks) + 2):
        changed = False
        for hook in hooks:
            before = _snapshot(hook)
            for call in hook.scope.hook_calls:
                callee = _resolve_named(analysis, hook.file, call.hook, "hooks")
                _apply_hook_call(hook.scope, call, callee, owner=hook)
            _resolve_derived(hook.scope.bindings)
            hook.server_write_params |= (
                set(hook.scope.params) & hook.scope.fetch_referenced)
            returns = _compute_returns(hook)
            if returns.get("kind") == "forward":
                inner = _resolve_named(
                    analysis, hook.file, returns["hook"], "hooks")
                returns = dict(inner.returns) if inner is not None \
                    and inner.returns else {"kind": "none"}
            hook.returns = returns
            if _snapshot(hook) != before:
                changed = True
        if not changed:
            return


# ------------------------------------------------------------ the graph


_ORIGIN_KINDS = ("server-state", "local-state", "setter", "dispatch",
                 "query", "context", "local")


def _forward_targets(comp: ComponentFacts, prop: str,
                     ) -> list[tuple[PropPass, str, bool]]:
    """Passes of `comp` that forward the received prop `prop` onward,
    verbatim or as a derived value: (pass, next prop name, derived?)."""
    out: list[tuple[PropPass, str, bool]] = []
    for p in comp.passes:
        if p.spread:
            if p.source == comp.props_param:
                out.append((p, prop, False))
            elif p.source == comp.rest_param and prop not in comp.props:
                out.append((p, prop, False))
            continue
        matched = None
        for source in p.sources:
            if source == prop and comp.receives(prop):
                matched = (p.attr, p.derived)
                break
            binding = comp.bindings.get(source)
            if binding is not None and prop in binding.prop_roots:
                matched = (p.attr, True)
                break
        if matched:
            out.append((p, matched[0], matched[1]))
    return out


def _extend_chain(analysis: ProjectAnalysis, hops: list[ChainHop],
                  comp: ComponentFacts | None, prop: str,
                  visited: frozenset[tuple[str, str]],
                  ) -> Iterator[list[ChainHop]]:
    if comp is None:
        yield hops
        return
    nexts: list[tuple[PropPass, str, bool, ComponentFacts | None]] = []
    for p, attr, derived in _forward_targets(comp, prop):
        child = _resolve_named(analysis, comp.file, p.child, "components")
        nexts.append((p, attr, derived, child))
    if not nexts:
        yield hops
        return
    for p, attr, derived, child in nexts:
        hop = ChainHop(component=p.child, prop=attr, line=p.line, derived=derived)
        key = (p.child, attr)
        if child is None or key in visited:
            yield hops + [hop]
        else:
            yield from _extend_chain(
                analysis, hops + [hop], child, attr, visited | {key})


def _chain_start_binding(comp: ComponentFacts, p: PropPass) -> Binding | None:
    """The strongest origin binding among a pass's candidate sources."""
    candidates = [
        comp.bindings[s] for s in p.sources
        if s in comp.bindings and comp.bindings[s].origin in _ORIGIN_KINDS
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda b: (_priority(b.origin), b.name))


def _build_chains(analysis: ProjectAnalysis) -> list[PropChain]:
    chains: list[PropChain] = []
    for name in sorted(analysis.components):
        comp = analysis.components[name]
        for p in sorted(comp.passes, key=lambda x: (x.line, x.col, x.attr)):
            if p.spread:
                continue
            binding = _chain_start_binding(comp, p)
            if binding is None:
                continue
            child = _resolve_named(analysis, comp.file, p.child, "components")
            first = ChainHop(component=p.child, prop=p.attr, line=p.line,
                             derived=p.derived)
            visited = frozenset({(comp.name, binding.name), (p.child, p.attr)})
            for hops in _extend_chain(analysis, [first], child, p.attr, visited):
                chains.append(PropChain(
                    origin_component=comp.name,
                    origin_file=comp.file,
                    source=binding.name,
                    origin=binding.origin,
                    line=binding.line,
                    col=binding.col,
                    hops=hops,
                    origin_root=binding.origin_root,
                ))
    chains.sort(key=lambda c: (c.origin_file, c.line, c.origin_component,
                               c.source, -c.depth, c.path()))
    return chains


def analyze_project(files: dict[str, str], language: str = "tsx",
                    fetch_names: tuple[str, ...] = _DEFAULT_FETCH_NAMES,
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
    _resolve_hooks(analysis)
    for name in sorted(analysis.components):
        comp = analysis.components[name]
        for call in comp.hook_calls:
            hook = _resolve_named(analysis, comp.file, call.hook, "hooks")
            _apply_hook_call(comp, call, hook, owner=None)
        _resolve_derived(comp.bindings)
    analysis.chains = _build_chains(analysis)
    return analysis


def analyze_source(code: str, language: str = "tsx",
                   fetch_names: tuple[str, ...] = _DEFAULT_FETCH_NAMES,
                   ) -> ProjectAnalysis:
    """Analyze a single string, honoring `// @file:` virtual-file markers."""
    return analyze_project(split_virtual_files(code, language),
                           language=language, fetch_names=fetch_names)


# --------------------------------------------------------- semantic checks


def _deepest_per_origin(chains: list[PropChain]) -> list[PropChain]:
    """One chain (the deepest) per (component, identifier) origin."""
    best: dict[tuple[str, str], PropChain] = {}
    for chain in chains:
        key = (chain.origin_component, chain.source)
        cur = best.get(key)
        if cur is None or chain.depth > cur.depth \
                or (chain.depth == cur.depth and chain.path() < cur.path()):
            best[key] = chain
    return [best[k] for k in sorted(best)]


def check_server_state_drilling(analysis: ProjectAnalysis,
                                params: dict[str, Any]) -> list[SemanticFinding]:
    """Server-origin state (fetch-in-effect -> setState, directly or inside
    a custom hook) drilled >= max_depth component levels: the data should
    live in a server-state library (React Query / SWR) and be read where it
    is used."""
    max_depth = int(params.get("max_depth", 3))
    findings = []
    matching = [c for c in analysis.chains
                if c.origin == "server-state" and c.depth >= max_depth]
    for chain in _deepest_per_origin(matching):
        findings.append(SemanticFinding(
            file=chain.origin_file, line=chain.line, col=chain.col,
            detail=(
                f"{chain.describe_source()} is server state (fetched in an "
                f"effect reachable from {chain.origin_component}) drilled "
                f"through {chain.depth} component levels: {chain.path()}"
            ),
            snippet=chain.path(),
        ))
    return findings


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
                deepest = _deepest_per_origin(setter_chains)
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


def check_prop_drilling(analysis: ProjectAnalysis,
                        params: dict[str, Any]) -> list[SemanticFinding]:
    """Any value drilled >= max_depth component levels, regardless of
    origin. `origins` (list) restricts which origin kinds count."""
    max_depth = int(params.get("max_depth", 3))
    origins = params.get("origins")
    allowed = set(origins) if isinstance(origins, (list, tuple)) else None
    matching = [
        c for c in analysis.chains
        if c.depth >= max_depth and (allowed is None or c.origin in allowed)
    ]
    findings = []
    for chain in _deepest_per_origin(matching):
        findings.append(SemanticFinding(
            file=chain.origin_file, line=chain.line, col=chain.col,
            detail=(
                f"{chain.describe_source()} ({chain.origin}) is drilled "
                f"through {chain.depth} component levels: {chain.path()}"
            ),
            snippet=chain.path(),
        ))
    return findings


SemanticCheck = Callable[[ProjectAnalysis, dict[str, Any]], list[SemanticFinding]]

SEMANTIC_CHECKS: dict[str, SemanticCheck] = {
    "react-server-state-drilling": check_server_state_drilling,
    "react-shared-mutable-state": check_shared_mutable_state,
    "react-prop-drilling": check_prop_drilling,
}


def semantic_check_names() -> list[str]:
    return sorted(SEMANTIC_CHECKS)


def run_semantic_check(name: str, analysis: ProjectAnalysis,
                       params: dict[str, Any] | None = None,
                       ) -> list[SemanticFinding]:
    check = SEMANTIC_CHECKS.get(name)
    if check is None:
        raise KeyError(
            f"unknown semantic check {name!r}; available: "
            + ", ".join(semantic_check_names()))
    return check(analysis, params or {})
