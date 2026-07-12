"""Cross-file React semantic analysis.

Single-file tree-sitter queries cannot express conventions that depend on
*several places at once* — e.g. "a prop drilled 3+ levels whose value came
from a fetch-in-effect must move to React Query". This module builds a
deterministic, LLM-free model of a React project and runs registered
semantic checks over it:

    per-file facts   components (capitalized functions rendering JSX),
                     received props, hook bindings (useState / useEffect /
                     useQuery / useContext / useReducer), and render edges
                     (`<Child data={x} />`)
    provenance       every value passed as a JSX attribute is classified:
                     server-state (useState fed by fetch/axios inside an
                     effect), local-state, setter, dispatch, query
                     (React Query/SWR), context, local, or prop passthrough
    prop chains      DFS across the resolved component graph following
                     passthrough edges — depth = how many component
                     boundaries the value crosses

Determinism: the same set of files always yields the same findings in the
same order. Chain following is conservative — only plain identifiers,
member accesses rooted at a received prop, and `{...props}` spreads
continue a chain; a value transformed by a call breaks it (missing a chain
beats inventing one).

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

_DEFAULT_FETCH_NAMES = ("fetch", "axios")

_JSX_NODE_TYPES = ("jsx_element", "jsx_self_closing_element", "jsx_fragment")

_DEFAULT_VIRTUAL_FILE = {
    "javascript": "Main.jsx",
    "typescript": "Main.ts",
    "tsx": "Main.tsx",
}


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
    """Provenance of an identifier inside a component."""

    name: str
    origin: str  # server-state | local-state | setter | dispatch | query
    #            # | context | local | prop
    line: int
    col: int
    partner: str | None = None  # setter <-> state value


@dataclass
class PropPass:
    """One JSX attribute edge: value leaving a component into a child."""

    child: str
    attr: str  # '*' for a spread
    source: str  # local identifier the value is rooted at
    line: int
    col: int
    spread: bool = False


@dataclass
class ComponentFacts:
    name: str
    file: str
    line: int
    col: int
    props: list[str] = field(default_factory=list)  # destructured names
    props_param: str | None = None  # `(props)` style parameter
    rest_param: str | None = None  # `{a, ...rest}` rest name
    bindings: dict[str, Binding] = field(default_factory=dict)
    passes: list[PropPass] = field(default_factory=list)
    hooks: set[str] = field(default_factory=set)

    def receives(self, prop: str) -> bool:
        return prop in self.props or self.props_param is not None


@dataclass
class FileFacts:
    path: str
    language: str
    syntax_ok: bool
    components: dict[str, ComponentFacts] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)  # local -> module


@dataclass
class ChainHop:
    component: str
    prop: str
    line: int


@dataclass
class PropChain:
    origin_component: str
    origin_file: str
    source: str  # identifier at the origin
    origin: str  # binding origin kind
    line: int
    col: int
    hops: list[ChainHop]

    @property
    def depth(self) -> int:
        return len(self.hops)

    def path(self) -> str:
        parts = [self.origin_component]
        for hop in self.hops:
            parts.append(f"-[{hop.prop}]-> {hop.component}")
        return " ".join(parts)


@dataclass
class ProjectAnalysis:
    files: dict[str, FileFacts]
    components: dict[str, ComponentFacts]
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
    Anything else (calls, literals, ternaries) returns (None, []) — a
    transformed value deliberately breaks provenance.
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


def _function_body(func: Node) -> Node | None:
    return func.child_by_field_name("body")


def _unwrap_parameter(node: Node) -> Node:
    """tsx wraps parameters in required_parameter/optional_parameter."""
    if node.type in ("required_parameter", "optional_parameter"):
        for child in node.named_children:
            if child.type in ("object_pattern", "identifier", "array_pattern"):
                return child
    return node


def _extract_props(func: Node, comp: ComponentFacts) -> None:
    params = func.child_by_field_name("parameters")
    first: Node | None = None
    if params is not None:
        named = [n for n in params.named_children if n.type != "comment"]
        first = named[0] if named else None
    elif func.type == "arrow_function":
        first = func.child_by_field_name("parameter")
    if first is None:
        return
    first = _unwrap_parameter(first)
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
        comp.bindings.setdefault(
            prop,
            Binding(prop, "prop", first.start_point[0] + 1, first.start_point[1]),
        )


def _call_callee_root(call: Node) -> str | None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    root, _ = _root_identifier(fn)
    return root


def _effect_marks_server_state(effect_arg: Node, comp: ComponentFacts,
                               fetch_names: tuple[str, ...]) -> None:
    """Inside one useEffect callback: if a fetch-like call appears, every
    state whose setter is referenced in the same effect is server-origin."""
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
    for binding in comp.bindings.values():
        if binding.origin == "setter" and binding.name in referenced:
            state = comp.bindings.get(binding.partner or "")
            if state is not None and state.origin == "local-state":
                state.origin = "server-state"


def _extract_hooks(body: Node, comp: ComponentFacts,
                   fetch_names: tuple[str, ...]) -> None:
    declarators = [n for n in _walk(body) if n.type == "variable_declarator"]
    effects: list[Node] = []
    for node in _walk(body):
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is not None and callee.type == "identifier" \
                and _text(callee) == "useEffect":
            args = node.child_by_field_name("arguments")
            if args is not None and args.named_children:
                effects.append(args.named_children[0])
            comp.hooks.add("useEffect")

    for decl in declarators:
        name_node = decl.child_by_field_name("name")
        value = decl.child_by_field_name("value")
        if name_node is None or value is None:
            continue
        if value.type != "call_expression":
            # `const { user } = props` re-binds received props
            if comp.props_param is not None and name_node.type == "object_pattern" \
                    and value.type == "identifier" \
                    and _text(value) == comp.props_param:
                for entry in name_node.named_children:
                    if entry.type == "shorthand_property_identifier_pattern":
                        prop = _text(entry)
                        comp.props.append(prop)
                        comp.bindings.setdefault(prop, Binding(
                            prop, "prop",
                            entry.start_point[0] + 1, entry.start_point[1]))
            elif name_node.type == "identifier":
                comp.bindings.setdefault(_text(name_node), Binding(
                    _text(name_node), "local",
                    name_node.start_point[0] + 1, name_node.start_point[1]))
            continue
        callee = value.child_by_field_name("function")
        hook = _text(callee) if callee is not None and callee.type == "identifier" else ""
        line, col = name_node.start_point[0] + 1, name_node.start_point[1]
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
        elif hook in _HOOK_ORIGINS:
            origin = _HOOK_ORIGINS[hook]
            if name_node.type == "identifier":
                comp.bindings[_text(name_node)] = Binding(
                    _text(name_node), origin, line, col)
            elif name_node.type == "object_pattern":
                for entry in name_node.named_children:
                    if entry.type == "shorthand_property_identifier_pattern":
                        comp.bindings[_text(entry)] = Binding(
                            _text(entry), origin, line, col)
                    elif entry.type == "pair_pattern":
                        v = entry.child_by_field_name("value")
                        if v is not None and v.type == "identifier":
                            comp.bindings[_text(v)] = Binding(
                                _text(v), origin, line, col)
            comp.hooks.add(hook)
        elif name_node.type == "identifier":
            comp.bindings.setdefault(_text(name_node), Binding(
                _text(name_node), "local", line, col))

    for effect in effects:
        _effect_marks_server_state(effect, comp, fetch_names)


def _jsx_pass_source(comp: ComponentFacts, expr: Node) -> str | None:
    """Normalize a JSX attribute expression to the local identifier (or
    received-prop name) it is rooted at; None breaks the chain."""
    root, path = _root_identifier(expr)
    if root is None:
        return None
    if comp.props_param is not None and root == comp.props_param:
        if not path:
            return None  # whole props object passed as one attribute value
        prop = path[0]
        comp.bindings.setdefault(prop, Binding(
            prop, "prop", expr.start_point[0] + 1, expr.start_point[1]))
        if prop not in comp.props:
            comp.props.append(prop)
        return prop
    return root


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
        for attr in node.named_children:
            if attr.type == "jsx_attribute":
                key = next((n for n in attr.named_children
                            if n.type == "property_identifier"), None)
                expr_wrap = next((n for n in attr.named_children
                                  if n.type == "jsx_expression"), None)
                if key is None or expr_wrap is None or not expr_wrap.named_children:
                    continue
                source = _jsx_pass_source(comp, expr_wrap.named_children[0])
                if source is None:
                    continue
                comp.passes.append(PropPass(
                    child=child_name, attr=_text(key), source=source,
                    line=attr.start_point[0] + 1, col=attr.start_point[1]))
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
                        child=child_name, attr="*", source=name,
                        line=attr.start_point[0] + 1, col=attr.start_point[1],
                        spread=True))


def _component_candidates(root: Node) -> Iterator[tuple[str, Node, Node]]:
    """Yield (name, name_node, function_node) for top-level components."""
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
    for name, name_node, func in _component_candidates(tree.root_node):
        if not name[:1].isupper():
            continue
        body = _function_body(func) or func
        if not _contains_jsx(body):
            continue
        comp = ComponentFacts(
            name=name, file=path,
            line=name_node.start_point[0] + 1, col=name_node.start_point[1])
        _extract_props(func, comp)
        _extract_hooks(body, comp, fetch_names)
        _extract_passes(body, comp)
        facts.components[name] = comp
    return facts


# ------------------------------------------------------------ the graph


def _resolve(analysis: ProjectAnalysis, parent: ComponentFacts,
             child_name: str) -> ComponentFacts | None:
    """Deterministic component resolution: same file, then the import's
    module basename, then a unique global name match."""
    file_facts = analysis.files.get(parent.file)
    if file_facts is not None:
        local = file_facts.components.get(child_name)
        if local is not None:
            return local
        module = file_facts.imports.get(child_name)
        if module:
            stem = Path(module).name
            for path in sorted(analysis.files):
                if Path(path).stem == stem:
                    comp = analysis.files[path].components.get(child_name)
                    if comp is not None:
                        return comp
    return analysis.components.get(child_name)


_ORIGIN_KINDS = ("server-state", "local-state", "setter", "dispatch",
                 "query", "context", "local")


def _forward_targets(comp: ComponentFacts, prop: str) -> list[tuple[PropPass, str]]:
    """Passes of `comp` that forward the received prop `prop` onward."""
    out: list[tuple[PropPass, str]] = []
    for p in comp.passes:
        if p.spread:
            if p.source == comp.props_param:
                out.append((p, prop))
            elif p.source == comp.rest_param and prop not in comp.props:
                out.append((p, prop))
        elif p.source == prop and comp.receives(prop):
            out.append((p, p.attr))
    return out


def _extend_chain(analysis: ProjectAnalysis, hops: list[ChainHop],
                  comp: ComponentFacts | None, prop: str,
                  visited: frozenset[tuple[str, str]],
                  ) -> Iterator[list[ChainHop]]:
    if comp is None:
        yield hops
        return
    nexts: list[tuple[PropPass, str, ComponentFacts | None]] = []
    for p, attr in _forward_targets(comp, prop):
        child = _resolve(analysis, comp, p.child)
        nexts.append((p, attr, child))
    if not nexts:
        yield hops
        return
    for p, attr, child in nexts:
        hop = ChainHop(component=p.child, prop=attr, line=p.line)
        key = (p.child, attr)
        if child is None or key in visited:
            yield hops + [hop]
        else:
            yield from _extend_chain(
                analysis, hops + [hop], child, attr, visited | {key})


def _build_chains(analysis: ProjectAnalysis) -> list[PropChain]:
    chains: list[PropChain] = []
    for name in sorted(analysis.components):
        comp = analysis.components[name]
        for p in sorted(comp.passes, key=lambda x: (x.line, x.col, x.attr)):
            if p.spread:
                continue
            binding = comp.bindings.get(p.source)
            if binding is None or binding.origin not in _ORIGIN_KINDS:
                continue
            child = _resolve(analysis, comp, p.child)
            first = ChainHop(component=p.child, prop=p.attr, line=p.line)
            visited = frozenset({(comp.name, p.source), (p.child, p.attr)})
            for hops in _extend_chain(analysis, [first], child, p.attr, visited):
                chains.append(PropChain(
                    origin_component=comp.name,
                    origin_file=comp.file,
                    source=p.source,
                    origin=binding.origin,
                    line=binding.line,
                    col=binding.col,
                    hops=hops,
                ))
    chains.sort(key=lambda c: (c.origin_file, c.line, c.origin_component,
                               c.source, -c.depth, c.path()))
    return chains


def analyze_project(files: dict[str, str], language: str = "tsx",
                    fetch_names: tuple[str, ...] = _DEFAULT_FETCH_NAMES,
                    ) -> ProjectAnalysis:
    """Build the full cross-file model. Deterministic for a given input."""
    analysis = ProjectAnalysis(files={}, components={}, chains=[])
    for path in sorted(files):
        analysis.files[path] = extract_file_facts(
            path, files[path], language=None if language_for_path(path) else language,
            fetch_names=fetch_names)
    for path in sorted(analysis.files):
        for name, comp in analysis.files[path].components.items():
            analysis.components.setdefault(name, comp)
    analysis.chains = _build_chains(analysis)
    return analysis


def analyze_source(code: str, language: str = "tsx",
                   fetch_names: tuple[str, ...] = _DEFAULT_FETCH_NAMES,
                   ) -> ProjectAnalysis:
    """Analyze a single string, honoring `// @file:` virtual-file markers."""
    return analyze_project(split_virtual_files(code, language),
                           language=language, fetch_names=fetch_names)


# --------------------------------------------------------- semantic checks


def _fetch_names(params: dict[str, Any]) -> tuple[str, ...]:
    names = params.get("fetch_names")
    if isinstance(names, (list, tuple)) and names:
        return tuple(str(n) for n in names)
    return _DEFAULT_FETCH_NAMES


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
    """Server-origin state (fetch-in-effect -> setState) drilled >= max_depth
    component levels: the data should live in a server-state library
    (React Query / SWR) and be read where it is used."""
    max_depth = int(params.get("max_depth", 3))
    findings = []
    matching = [c for c in analysis.chains
                if c.origin == "server-state" and c.depth >= max_depth]
    for chain in _deepest_per_origin(matching):
        findings.append(SemanticFinding(
            file=chain.origin_file, line=chain.line, col=chain.col,
            detail=(
                f"'{chain.source}' is server state (fetched in an effect in "
                f"{chain.origin_component}) drilled through {chain.depth} "
                f"component levels: {chain.path()}"
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
            branches = {p.child for p in comp.passes
                        if not p.spread and p.source in (value_name, setter)}
            setter_passed = any(not p.spread and p.source == setter
                                for p in comp.passes)
            setter_depth = max(
                (c.depth for c in analysis.chains
                 if c.origin_component == name and c.source == setter),
                default=0)
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
                deepest = _deepest_per_origin(
                    [c for c in analysis.chains
                     if c.origin_component == name and c.source == setter])
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
                f"'{chain.source}' ({chain.origin}) is drilled through "
                f"{chain.depth} component levels: {chain.path()}"
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
