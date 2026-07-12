"""Framework-neutral core for cross-file data-flow analysis.

Frontend frameworks share one convention problem shape: a value originates
somewhere (server fetch, local state, a store), travels through component
boundaries as props/attributes, and may be transformed or wrapped along the
way. This module owns everything about that shape that is NOT tied to a
specific framework's syntax:

    model         Binding (identifier provenance), PropPass (render edge),
                  ComponentFacts / HookFacts / FileFacts, PropChain,
                  ProjectAnalysis, SemanticFinding
    provenance    origin priority, derived-value fixpoint (a value computed
                  from others inherits the strongest source's origin and
                  keeps the union of received-prop roots)
    hooks         bounded fixpoint over user-defined hooks/composables,
                  return-shape mapping (object / single / array / forwarded
                  hook) — the framework passes its own arg-promotion policy
    chains        DFS over the resolved component graph following verbatim
                  and derived forwards; depth = component boundaries crossed
    checks        generic checkers (server-state drilling, prop drilling)
                  plus the registry semantic rules dispatch through

Framework front-ends (react.py for JSX/hooks, vue.py for SFC/composition
API) extract facts into this model and register their checks under their
own names. Everything here is deterministic and LLM-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from tree_sitter import Node

_FILE_MARKER = re.compile(r"^[ \t]*//[ \t]*@file:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

_DEFAULT_VIRTUAL_FILE = {
    "javascript": "Main.jsx",
    "typescript": "Main.ts",
    "tsx": "Main.tsx",
    "vue": "Main.vue",
}

DEFAULT_FETCH_NAMES = ("fetch", "axios")

CUSTOM_HOOK_NAME = re.compile(r"^use[A-Z0-9_]")

_JSX_NODE_TYPES = ("jsx_element", "jsx_self_closing_element", "jsx_fragment")

FUNCTION_NODE_TYPES = ("arrow_function", "function_expression",
                       "function_declaration", "function")

# strongest first: a derived value inherits its strongest source's origin
_ORIGIN_PRIORITY = ("server-state", "query", "context", "local-state",
                    "dispatch", "setter", "prop", "local")

# origins a chain may START from ('prop' continues chains, never starts one)
ORIGIN_KINDS = ("server-state", "local-state", "setter", "dispatch",
                "query", "context", "local")


def priority(origin: str) -> int:
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
    """One render edge: value(s) leaving a component into a child."""

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
    """A call to a user-defined hook/composable inside a component or
    another hook."""

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
    props_param: str | None = None  # `(props)` style parameter / props object
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
    """A user-defined hook/composable; bindings live in `scope` (same
    machinery as components, minus render edges)."""

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


# ----------------------------------------------------------- tree helpers


def text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from walk(child)


def root_identifier(node: Node) -> tuple[str | None, list[str]]:
    """Root identifier of an expression plus the member path below it.

    `user` -> ('user', []); `props.user.name` -> ('props', ['user', 'name']).
    Anything else (calls, literals, ternaries) returns (None, []) — those go
    through candidate collection (`expr_candidates`) as derived values.
    """
    path: list[str] = []
    while node.type in ("member_expression", "subscript_expression"):
        if node.type == "member_expression":
            prop = node.child_by_field_name("property")
            if prop is not None:
                path.insert(0, text(prop))
        obj = node.child_by_field_name("object")
        if obj is None:
            return None, []
        node = obj
    if node.type == "identifier":
        return text(node), path
    return None, []


def note_prop(comp: ComponentFacts, prop: str, node: Node) -> None:
    """Register a received prop discovered lazily (e.g. `props.user`)."""
    comp.bindings.setdefault(prop, Binding(
        prop, "prop", node.start_point[0] + 1, node.start_point[1],
        prop_roots=(prop,)))
    if prop not in comp.props:
        comp.props.append(prop)


def expr_candidates(expr: Node, comp: ComponentFacts) -> tuple[str, ...]:
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
            root, path = root_identifier(node)
            if root is not None:
                if comp.props_param is not None and root == comp.props_param:
                    if path:
                        note_prop(comp, path[0], node)
                        out.append(path[0])
                else:
                    out.append(root)
                return  # a resolvable chain: don't double-count its root
            # chain rooted in a call (`data.filter(x).map(y)`): descend
        elif node.type == "identifier":
            parent = node.parent
            if parent is not None and parent.type in FUNCTION_NODE_TYPES \
                    and parent.child_by_field_name("body") != node:
                return  # arrow parameter
            name = text(node)
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


def function_body(func: Node) -> Node | None:
    return func.child_by_field_name("body")


def unwrap_parameter(node: Node) -> Node:
    """ts/tsx wraps parameters in required_parameter/optional_parameter."""
    if node.type in ("required_parameter", "optional_parameter"):
        for child in node.named_children:
            if child.type in ("object_pattern", "identifier", "array_pattern"):
                return child
    return node


def parameter_nodes(func: Node) -> list[Node]:
    params = func.child_by_field_name("parameters")
    if params is not None:
        return [unwrap_parameter(n) for n in params.named_children
                if n.type != "comment"]
    single = func.child_by_field_name("parameter")
    return [unwrap_parameter(single)] if single is not None else []


def call_callee_root(call: Node) -> str | None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    root, _ = root_identifier(fn)
    return root


def pattern_targets(name_node: Node,
                    ) -> tuple[str | None, tuple[tuple[str, str], ...]]:
    """(kind, ((key_or_index, local_name), ...)) for a declarator LHS."""
    if name_node.type == "identifier":
        return "identifier", (("", text(name_node)),)
    if name_node.type == "object_pattern":
        entries: list[tuple[str, str]] = []
        for entry in name_node.named_children:
            if entry.type == "shorthand_property_identifier_pattern":
                entries.append((text(entry), text(entry)))
            elif entry.type == "pair_pattern":
                key = entry.child_by_field_name("key")
                value = entry.child_by_field_name("value")
                if key is not None and value is not None \
                        and value.type == "identifier":
                    entries.append((text(key), text(value)))
        return "object", tuple(entries)
    if name_node.type == "array_pattern":
        idents = [n for n in name_node.named_children if n.type == "identifier"]
        return "array", tuple((str(i), text(n)) for i, n in enumerate(idents))
    return None, ()


def extract_returns_spec(func: Node, body: Node) -> tuple:
    """Shape of a hook/composable's own (non-nested) return value."""
    def own_returns(node: Node) -> Iterator[Node]:
        for child in node.named_children:
            if child.type in FUNCTION_NODE_TYPES:
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
                entries.append((text(entry), text(entry)))
            elif entry.type == "pair":
                key = entry.child_by_field_name("key")
                value = entry.child_by_field_name("value")
                if key is not None and value is not None \
                        and value.type == "identifier":
                    entries.append((text(key), text(value)))
        return ("object", tuple(entries))
    if expr.type == "identifier":
        return ("identifier", text(expr))
    if expr.type == "array":
        return ("array", tuple(
            text(n) if n.type == "identifier" else ""
            for n in expr.named_children))
    if expr.type == "call_expression":
        callee = call_callee_root(expr)
        if callee:
            return ("call", callee)
    return ("none",)


def top_level_functions(root: Node) -> Iterator[tuple[str, Node, Node]]:
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
                yield text(name_node), name_node, target
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
                    yield text(name_node), name_node, value


def extract_imports(root: Node) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in root.named_children:
        if node.type != "import_statement":
            continue
        source = node.child_by_field_name("source")
        module = ""
        if source is not None:
            frag = next((n for n in source.named_children
                         if n.type == "string_fragment"), None)
            module = text(frag) if frag is not None else text(source).strip("'\"")
        for n in walk(node):
            if n.type == "import_specifier":
                alias = n.child_by_field_name("alias")
                name = alias if alias is not None else n.child_by_field_name("name")
                if name is not None:
                    imports[text(name)] = module
            elif n.type == "identifier" and n.parent is not None \
                    and n.parent.type == "import_clause":
                imports[text(n)] = module  # default import
    return imports


# ----------------------------------------------------- derived provenance


def resolve_derived(bindings: dict[str, Binding]) -> None:
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
            best = min(deps, key=lambda d: (priority(d.origin), d.name))
            new_origin = best.origin if priority(best.origin) < priority("local") \
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


# ------------------------------------------------- hook/composable engine


def resolve_named(analysis: ProjectAnalysis, from_file: str, name: str,
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
            stem = Path(module).stem  # './Layout' and './UserCard.vue' alike
            for path in sorted(analysis.files):
                if Path(path).stem == stem:
                    found = getattr(analysis.files[path], registry_attr).get(name)
                    if found is not None:
                        return found
    registry = getattr(analysis, registry_attr)
    return registry.get(name)


def compute_returns(hook: HookFacts) -> dict[str, Any]:
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
    return {"kind": "none"}


def best_of(origins: list[str]) -> str:
    if not origins:
        return "local"
    return min(origins, key=priority)


def apply_hook_returns(scope: ComponentFacts, call: HookCall,
                       hook: HookFacts) -> None:
    """Push a resolved hook's return origins into the caller's bindings."""
    returns = hook.returns
    if not call.target_kind or not returns:
        return
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
            origin = best_of([e["origin"]
                              for e in returns.get("entries", {}).values()])
        elif kind == "array":
            origin = best_of([i["origin"] for i in returns.get("items", [])])
        else:
            origin = "local"
        scope.bindings[local] = Binding(local, origin, call.line, call.col)


# framework hook-call policy: (caller scope, call, resolved hook or None,
# owning hook when the caller is itself a hook)
ApplyHookCall = Callable[
    [ComponentFacts, HookCall, HookFacts | None, HookFacts | None], None]


def _snapshot(hook: HookFacts) -> tuple:
    return (
        tuple(sorted((b.name, b.origin, b.partner or "")
                     for b in hook.scope.bindings.values())),
        tuple(sorted(hook.server_write_params)),
        repr(hook.returns),
    )


def resolve_hooks(analysis: ProjectAnalysis, apply_call: ApplyHookCall,
                  forward_origins: dict[str, str] | None = None) -> None:
    """Bounded fixpoint over user-defined hooks/composables (they may call
    each other, in any file order). `forward_origins` maps well-known hook
    names (useQuery, ...) to origins for `return useQuery(...)` forwards."""
    forward_origins = forward_origins or {}
    hooks = [analysis.hooks[name] for name in sorted(analysis.hooks)]
    for _ in range(len(hooks) + 2):
        changed = False
        for hook in hooks:
            before = _snapshot(hook)
            for call in hook.scope.hook_calls:
                callee = resolve_named(analysis, hook.file, call.hook, "hooks")
                apply_call(hook.scope, call, callee, hook)
            resolve_derived(hook.scope.bindings)
            hook.server_write_params |= (
                set(hook.scope.params) & hook.scope.fetch_referenced)
            spec = hook.scope.returns_spec
            if spec and spec[0] == "call":
                callee_name = spec[1]
                if callee_name in forward_origins:
                    returns: dict[str, Any] = {
                        "kind": "single", "origin": forward_origins[callee_name]}
                else:
                    inner = resolve_named(
                        analysis, hook.file, callee_name, "hooks")
                    returns = dict(inner.returns) if inner is not None \
                        and inner.returns else {"kind": "none"}
            else:
                returns = compute_returns(hook)
            hook.returns = returns
            if _snapshot(hook) != before:
                changed = True
        if not changed:
            return


def resolve_components(analysis: ProjectAnalysis,
                       apply_call: ApplyHookCall) -> None:
    """After hooks are stable: push hook results into components and settle
    derived provenance."""
    for name in sorted(analysis.components):
        comp = analysis.components[name]
        for call in comp.hook_calls:
            hook = resolve_named(analysis, comp.file, call.hook, "hooks")
            apply_call(comp, call, hook, None)
        resolve_derived(comp.bindings)


# ------------------------------------------------------------ the chains


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
        child = resolve_named(analysis, comp.file, p.child, "components")
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
        if s in comp.bindings and comp.bindings[s].origin in ORIGIN_KINDS
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda b: (priority(b.origin), b.name))


def build_chains(analysis: ProjectAnalysis) -> list[PropChain]:
    chains: list[PropChain] = []
    for name in sorted(analysis.components):
        comp = analysis.components[name]
        for p in sorted(comp.passes, key=lambda x: (x.line, x.col, x.attr)):
            if p.spread:
                continue
            binding = _chain_start_binding(comp, p)
            if binding is None:
                continue
            child = resolve_named(analysis, comp.file, p.child, "components")
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


# --------------------------------------------------------- generic checks


def deepest_per_origin(chains: list[PropChain]) -> list[PropChain]:
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
    """Server-origin state (fetch-in-effect, directly or inside a custom
    hook/composable) drilled >= max_depth component levels: the data should
    live in a server-state library and be read where it is used."""
    max_depth = int(params.get("max_depth", 3))
    findings = []
    matching = [c for c in analysis.chains
                if c.origin == "server-state" and c.depth >= max_depth]
    for chain in deepest_per_origin(matching):
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
    for chain in deepest_per_origin(matching):
        findings.append(SemanticFinding(
            file=chain.origin_file, line=chain.line, col=chain.col,
            detail=(
                f"{chain.describe_source()} ({chain.origin}) is drilled "
                f"through {chain.depth} component levels: {chain.path()}"
            ),
            snippet=chain.path(),
        ))
    return findings


# --------------------------------------------------------------- registry


SemanticCheck = Callable[[ProjectAnalysis, dict[str, Any]], list[SemanticFinding]]

# populated by the framework modules (react.py, vue.py) at import time
SEMANTIC_CHECKS: dict[str, SemanticCheck] = {}


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
