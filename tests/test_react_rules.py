"""Cross-file React semantic rules: analyzer, checkers, engine, store, MCP, CLI.

The point under test: verdicts that need *several places at once* —
prop-drilling depth across component boundaries combined with where the
data came from (fetch-in-effect vs React Query vs local useState).
"""

import json
from pathlib import Path

import pytest

from acode.astcore.react import (
    analyze_project,
    analyze_source,
    extract_file_facts,
    run_semantic_check,
    semantic_check_names,
    split_virtual_files,
)
from acode.astcore.rules import Rule, RuleEngine, RuleError, validate_rule
from acode.rag.store import Convention, ConventionStore

from tests.conftest import REPO_ROOT

ENGINE = RuleEngine()

REACT_JSON = REPO_ROOT / "conventions" / "react.json"


# --------------------------------------------------------------- fixtures

BAD_PROJECT = {
    "App.tsx": """
import { useState, useEffect } from "react";
import Layout from "./Layout";
import Toolbar from "./Toolbar";

export function App() {
  const [user, setUser] = useState(null);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    fetch("/api/user").then((r) => r.json()).then(setUser);
  }, []);
  return (
    <div>
      <Toolbar filter={filter} onFilter={setFilter} />
      <Layout user={user} filter={filter} />
    </div>
  );
}
""",
    "Layout.tsx": """
import Sidebar from "./Sidebar";
export default function Layout({ user, filter }) {
  return <Sidebar user={user} filter={filter} />;
}
""",
    "Sidebar.tsx": """
import UserCard from "./UserCard";
const Sidebar = ({ user, filter }) => <UserCard user={user} label={filter} />;
export default Sidebar;
""",
    "UserCard.tsx": """
export default function UserCard({ user, label }) {
  return <div>{user.name}{label}</div>;
}
""",
}

GOOD_PROJECT = {
    "App.tsx": """
import { useState } from "react";
import { FilterContext } from "./filter-context";
import Layout from "./Layout";

export function App() {
  const [filter, setFilter] = useState("");
  return (
    <FilterContext.Provider value={{ filter, setFilter }}>
      <Layout />
    </FilterContext.Provider>
  );
}
""",
    "Layout.tsx": """
import UserCard from "./UserCard";
export default function Layout() {
  return <UserCard />;
}
""",
    "UserCard.tsx": """
import { useQuery } from "@tanstack/react-query";
export default function UserCard() {
  const { data: user } = useQuery({ queryKey: ["user"] });
  return <div>{user.name}</div>;
}
""",
}


@pytest.fixture()
def bad_analysis():
    return analyze_project(BAD_PROJECT)


# ------------------------------------------------------ virtual file split


class TestVirtualFiles:
    def test_no_marker_is_one_file(self):
        assert split_virtual_files("const x = 1;\n") == {"Main.tsx": "const x = 1;\n"}

    def test_markers_split(self):
        code = "// @file: A.tsx\nconst a = 1;\n// @file: b/B.tsx\nconst b = 2;\n"
        files = split_virtual_files(code)
        assert files == {"A.tsx": "const a = 1;\n", "b/B.tsx": "const b = 2;\n"}

    def test_head_before_first_marker_kept(self):
        files = split_virtual_files("const h = 0;\n// @file: A.tsx\nconst a = 1;\n")
        assert files["Main.tsx"] == "const h = 0;\n"
        assert files["A.tsx"] == "const a = 1;\n"

    def test_default_name_follows_language(self):
        assert "Main.jsx" in split_virtual_files("x", "javascript")


# ------------------------------------------------------------- extraction


class TestExtraction:
    def test_component_forms(self):
        facts = extract_file_facts("X.tsx", """
export function Decl() { return <div />; }
const Arrow = () => <div />;
export default Arrow;
function helper() { return 1; }
function NoJsx() { return 1; }
""")
        assert set(facts.components) == {"Decl", "Arrow"}

    def test_destructured_props_and_rest(self):
        facts = extract_file_facts("X.tsx",
            "export function C({ a, b: renamed, ...rest }) { return <div />; }\n")
        comp = facts.components["C"]
        assert comp.props == ["a", "renamed"]
        assert comp.rest_param == "rest"

    def test_props_object_member_access_normalized(self):
        facts = extract_file_facts("X.tsx",
            "const C = (props) => <Child user={props.user} />;\n")
        comp = facts.components["C"]
        assert comp.props_param == "props"
        assert comp.passes[0].source == "user"
        assert comp.bindings["user"].origin == "prop"

    def test_props_destructured_from_body(self):
        facts = extract_file_facts("X.tsx", """
function C(props) {
  const { user } = props;
  return <Child user={user} />;
}
""")
        comp = facts.components["C"]
        assert comp.bindings["user"].origin == "prop"

    def test_use_state_pair(self):
        facts = extract_file_facts("X.tsx", """
import { useState } from "react";
function C() {
  const [count, setCount] = useState(0);
  return <div>{count}</div>;
}
""")
        comp = facts.components["C"]
        assert comp.bindings["count"].origin == "local-state"
        assert comp.bindings["count"].partner == "setCount"
        assert comp.bindings["setCount"].origin == "setter"

    def test_fetch_in_effect_promotes_to_server_state(self):
        facts = extract_file_facts("X.tsx", """
function C() {
  const [user, setUser] = useState(null);
  useEffect(() => {
    fetch("/api").then((r) => r.json()).then(setUser);
  }, []);
  return <div>{user}</div>;
}
""")
        assert facts.components["C"].bindings["user"].origin == "server-state"

    def test_axios_in_effect_counts_as_fetch(self):
        facts = extract_file_facts("X.tsx", """
function C() {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    axios.get("/api").then((r) => setRows(r.data));
  }, []);
  return <div>{rows}</div>;
}
""")
        assert facts.components["C"].bindings["rows"].origin == "server-state"

    def test_effect_without_fetch_stays_local(self):
        facts = extract_file_facts("X.tsx", """
function C() {
  const [open, setOpen] = useState(false);
  useEffect(() => { setOpen(true); }, []);
  return <div>{open}</div>;
}
""")
        assert facts.components["C"].bindings["open"].origin == "local-state"

    def test_use_query_binding_with_alias(self):
        facts = extract_file_facts("X.tsx", """
function C() {
  const { data: user } = useQuery({ queryKey: ["u"] });
  return <div>{user}</div>;
}
""")
        assert facts.components["C"].bindings["user"].origin == "query"

    def test_use_context_binding(self):
        facts = extract_file_facts("X.tsx", """
function C() {
  const theme = useContext(ThemeContext);
  return <div>{theme}</div>;
}
""")
        assert facts.components["C"].bindings["theme"].origin == "context"

    def test_call_result_becomes_derived_pass(self):
        facts = extract_file_facts("X.tsx",
            "const C = ({ items }) => <Child items={filterItems(items)} />;\n")
        p = facts.components["C"].passes[0]
        assert p.derived and "items" in p.sources

    def test_literal_only_expression_has_no_sources(self):
        facts = extract_file_facts("X.tsx",
            "const C = () => <Child items={[1, 2, 3]} />;\n")
        assert facts.components["C"].passes == []

    def test_host_elements_ignored(self):
        facts = extract_file_facts("X.tsx",
            "const C = ({ id }) => <div id={id} />;\n")
        assert facts.components["C"].passes == []

    def test_spread_of_props_recorded(self):
        facts = extract_file_facts("X.tsx",
            "const C = (props) => <Child {...props} />;\n")
        p = facts.components["C"].passes[0]
        assert p.spread and p.source == "props" and p.attr == "*"


# ------------------------------------------------------------------ chains


class TestChains:
    def test_cross_file_chain_depth(self, bad_analysis):
        user_chains = [c for c in bad_analysis.chains
                       if c.source == "user" and c.origin_component == "App"]
        assert len(user_chains) == 1
        chain = user_chains[0]
        assert chain.origin == "server-state"
        assert chain.depth == 3
        assert chain.path() == (
            "App -[user]-> Layout -[user]-> Sidebar -[user]-> UserCard")

    def test_rename_along_chain_tracked(self, bad_analysis):
        filter_deep = [c for c in bad_analysis.chains
                       if c.source == "filter" and c.depth == 3]
        assert filter_deep[0].hops[-1].prop == "label"  # filter -> label

    def test_unresolved_child_ends_chain(self):
        analysis = analyze_project({"App.tsx": """
function App() {
  const [x, setX] = useState(1);
  return <Mystery x={x} />;
}
"""})
        assert [c.depth for c in analysis.chains if c.source == "x"] == [1]

    def test_spread_continues_chain(self):
        analysis = analyze_project({
            "App.tsx": """
import Mid from "./Mid";
function App() {
  const [v, setV] = useState(1);
  return <Mid v={v} />;
}
""",
            "Mid.tsx": """
import Leaf from "./Leaf";
const Mid = (props) => <Leaf {...props} />;
export default Mid;
""",
            "Leaf.tsx": """
import Deep from "./Deep";
const Leaf = ({ v }) => <Deep v={v} />;
export default Leaf;
""",
            "Deep.tsx": "const Deep = ({ v }) => <div>{v}</div>;\nexport default Deep;\n",
        })
        depths = [c.depth for c in analysis.chains if c.source == "v"]
        assert max(depths) == 3

    def test_recursive_component_terminates(self):
        analysis = analyze_project({"Tree.tsx": """
export default function Tree({ node }) {
  return <Tree node={node} />;
}
"""})
        assert isinstance(analysis.chains, list)  # no infinite recursion

    def test_deterministic(self):
        a = analyze_project(BAD_PROJECT)
        b = analyze_project(dict(reversed(list(BAD_PROJECT.items()))))
        assert [c.path() for c in a.chains] == [c.path() for c in b.chains]


# -------------------------------------------------------- semantic checks


class TestSemanticChecks:
    def test_registry(self):
        assert semantic_check_names() == [
            "react-prop-drilling",
            "react-server-state-drilling",
            "react-shared-mutable-state",
        ]
        with pytest.raises(KeyError):
            run_semantic_check("nope", analyze_project({}), {})

    def test_server_state_drilling_fires_at_threshold(self, bad_analysis):
        findings = run_semantic_check(
            "react-server-state-drilling", bad_analysis, {"max_depth": 3})
        assert len(findings) == 1
        assert "React" not in findings[0].detail  # detail is factual, message advises
        assert "App -[user]-> Layout" in findings[0].detail
        assert findings[0].file == "App.tsx"

    def test_server_state_drilling_quiet_below_threshold(self, bad_analysis):
        assert run_semantic_check(
            "react-server-state-drilling", bad_analysis, {"max_depth": 4}) == []

    def test_local_state_not_reported_as_server(self, bad_analysis):
        findings = run_semantic_check(
            "react-server-state-drilling", bad_analysis, {"max_depth": 3})
        assert all("filter" not in f.detail for f in findings)

    def test_use_query_origin_is_exempt(self):
        analysis = analyze_project(GOOD_PROJECT)
        assert run_semantic_check(
            "react-server-state-drilling", analysis, {}) == []

    def test_shared_mutable_state_fan_out(self, bad_analysis):
        findings = run_semantic_check(
            "react-shared-mutable-state", bad_analysis,
            {"min_branches": 2, "max_setter_depth": 3})
        assert len(findings) == 1
        assert "filter" in findings[0].detail
        assert "Layout" in findings[0].detail and "Toolbar" in findings[0].detail

    def test_shared_mutable_state_deep_setter(self):
        analysis = analyze_project({
            "App.tsx": """
import A from "./A";
function App() {
  const [v, setV] = useState(1);
  return <A onChange={setV} />;
}
""",
            "A.tsx": "import B from \"./B\";\nconst A = ({ onChange }) => <B onChange={onChange} />;\nexport default A;\n",
            "B.tsx": "import C from \"./C\";\nconst B = ({ onChange }) => <C onChange={onChange} />;\nexport default B;\n",
            "C.tsx": "const C = ({ onChange }) => <button onClick={onChange} />;\nexport default C;\n",
        })
        findings = run_semantic_check(
            "react-shared-mutable-state", analysis, {"max_setter_depth": 3})
        assert len(findings) == 1
        assert "drilled 3 levels" in findings[0].detail

    def test_shared_state_context_is_exempt(self):
        analysis = analyze_project(GOOD_PROJECT)
        assert run_semantic_check("react-shared-mutable-state", analysis, {}) == []

    def test_value_only_fan_out_is_fine(self):
        # broadcast without a setter going down: no mutation from below
        analysis = analyze_project({"App.tsx": """
function App() {
  const [v, setV] = useState(1);
  return <div><A v={v} /><B v={v} /></div>;
}
const A = ({ v }) => <i>{v}</i>;
const B = ({ v }) => <i>{v}</i>;
"""})
        assert run_semantic_check("react-shared-mutable-state", analysis, {}) == []

    def test_generic_prop_drilling_origin_filter(self, bad_analysis):
        all_findings = run_semantic_check(
            "react-prop-drilling", bad_analysis, {"max_depth": 3})
        assert len(all_findings) == 2  # user + filter
        only_local = run_semantic_check(
            "react-prop-drilling", bad_analysis,
            {"max_depth": 3, "origins": ["local-state"]})
        assert len(only_local) == 1 and "'filter'" in only_local[0].detail


# --------------------------------------------------------- derived values


class TestDerivedValues:
    def test_declared_transform_inherits_origin(self):
        analysis = analyze_project({"App.tsx": """
function App() {
  const [data, setData] = useState(null);
  useEffect(() => { fetch("/api").then((r) => r.json()).then(setData); }, []);
  const rows = normalize(data);
  return <Grid rows={rows} />;
}
const Grid = ({ rows }) => <table>{rows}</table>;
"""})
        binding = analysis.components["App"].bindings["rows"]
        assert binding.origin == "server-state"
        assert binding.origin_root == "data"
        chain = next(c for c in analysis.chains if c.source == "rows")
        assert chain.origin == "server-state"

    def test_use_memo_inherits_origin(self):
        analysis = analyze_project({"App.tsx": """
function App() {
  const [items, setItems] = useState([]);
  const sorted = useMemo(() => [...items].sort(), [items]);
  return <List items={sorted} />;
}
const List = ({ items }) => <ul>{items}</ul>;
"""})
        assert analysis.components["App"].bindings["sorted"].origin == "local-state"

    def test_destructure_from_value_inherits(self):
        facts = extract_file_facts("X.tsx", """
function C() {
  const { data: payload } = useQuery({ queryKey: ["k"] });
  const { rows } = payload;
  return <div>{rows}</div>;
}
""")
        comp = facts.components["C"]
        assert comp.bindings["rows"].derived_from == ("payload",)

    def test_inline_transform_continues_chain(self):
        analysis = analyze_project({
            "App.tsx": """
import Layout from "./Layout";
function App() {
  const [user, setUser] = useState(null);
  useEffect(() => { fetch("/u").then((r) => r.json()).then(setUser); }, []);
  return <Layout user={user} />;
}
""",
            "Layout.tsx": """
import Card from "./Card";
export default function Layout({ user }) {
  return <Card profile={decorate(user)} />;
}
""",
            "Card.tsx": """
import Avatar from "./Avatar";
export default function Card({ profile }) {
  return <Avatar profile={profile} />;
}
""",
            "Avatar.tsx": "export default function Avatar({ profile }) { return <img alt={profile} />; }\n",
        })
        chain = max((c for c in analysis.chains if c.source == "user"),
                    key=lambda c: c.depth)
        assert chain.depth == 3
        assert chain.hops[1].derived  # the decorate() hop
        assert "~[profile]~>" in chain.path()

    def test_derived_local_declaration_forwards_chain(self):
        analysis = analyze_project({"App.tsx": """
function Mid({ user }) {
  const banner = makeBanner(user);
  return <Leaf banner={banner} />;
}
function App() {
  const [user, setUser] = useState(null);
  useEffect(() => { fetch("/u").then(setUser); }, []);
  return <Mid user={user} />;
}
const Leaf = ({ banner }) => <b>{banner}</b>;
"""})
        chain = max((c for c in analysis.chains if c.source == "user"),
                    key=lambda c: c.depth)
        assert chain.depth == 2 and chain.hops[1].derived

    def test_callback_wrapped_setter_counts_for_fan_out(self):
        analysis = analyze_project({"App.tsx": """
function App() {
  const [filter, setFilter] = useState("");
  return (
    <div>
      <Toolbar onChange={(v) => setFilter(v)} />
      <Results filter={filter} />
    </div>
  );
}
const Toolbar = ({ onChange }) => <input onChange={onChange} />;
const Results = ({ filter }) => <div>{filter}</div>;
"""})
        findings = run_semantic_check(
            "react-shared-mutable-state", analysis, {"min_branches": 2})
        assert len(findings) == 1
        assert "Toolbar" in findings[0].detail and "Results" in findings[0].detail

    def test_provider_value_is_not_a_pass(self):
        facts = extract_file_facts("X.tsx", """
function App() {
  const [v, setV] = useState(1);
  return <Ctx.Provider value={{ v, setV }}><Child /></Ctx.Provider>;
}
""")
        assert all(p.child == "Child" or False for p in facts.components["App"].passes)
        assert facts.components["App"].passes == []

    def test_mixed_sources_pick_strongest_origin(self):
        analysis = analyze_project({"App.tsx": """
function App() {
  const [user, setUser] = useState(null);
  const [note, setNote] = useState("");
  useEffect(() => { fetch("/u").then(setUser); }, []);
  return <Panel data={merge(user, note)} />;
}
const Panel = ({ data }) => <div>{data}</div>;
"""})
        chain = next(c for c in analysis.chains
                     if c.origin_component == "App" and c.hops[0].prop == "data")
        assert chain.source == "user" and chain.origin == "server-state"

    def test_derived_message_mentions_root(self):
        analysis = analyze_project({"App.tsx": """
import Layout from "./Layout";
function App() {
  const [data, setData] = useState(null);
  useEffect(() => { fetch("/d").then(setData); }, []);
  const rows = normalize(data);
  return <Layout rows={rows} />;
}
const Layout = ({ rows }) => <Mid rows={rows} />;
const Mid = ({ rows }) => <Leaf rows={rows} />;
const Leaf = ({ rows }) => <div>{rows}</div>;
"""})
        findings = run_semantic_check(
            "react-server-state-drilling", analysis, {"max_depth": 3})
        assert len(findings) == 1
        assert "'rows' (derived from 'data')" in findings[0].detail


# ------------------------------------------------------------ custom hooks


class TestCustomHooks:
    def test_hook_wrapping_fetch_effect_is_server_state(self):
        analysis = analyze_project({"App.tsx": """
function useUser() {
  const [user, setUser] = useState(null);
  useEffect(() => { fetch("/api/user").then((r) => r.json()).then(setUser); }, []);
  return { user, setUser };
}
function App() {
  const { user } = useUser();
  return <Layout user={user} />;
}
const Layout = ({ user }) => <Mid user={user} />;
const Mid = ({ user }) => <Leaf user={user} />;
const Leaf = ({ user }) => <div>{user}</div>;
"""})
        assert analysis.components["App"].bindings["user"].origin == "server-state"
        findings = run_semantic_check(
            "react-server-state-drilling", analysis, {"max_depth": 3})
        assert len(findings) == 1

    def test_hook_in_other_file_resolves_via_import(self):
        analysis = analyze_project({
            "useUser.ts": """
import { useState, useEffect } from "react";
export function useUser() {
  const [user, setUser] = useState(null);
  useEffect(() => { fetch("/u").then(setUser); }, []);
  return { user };
}
""",
            "App.tsx": """
import { useUser } from "./useUser";
export function App() {
  const { user } = useUser();
  return <div>{user}</div>;
}
""",
        })
        assert analysis.components["App"].bindings["user"].origin == "server-state"

    def test_hook_object_return_with_alias_and_partner(self):
        analysis = analyze_project({"App.tsx": """
function useFilter() {
  const [filter, setFilter] = useState("");
  return { filter, setFilter };
}
function App() {
  const { filter: f, setFilter: setF } = useFilter();
  return <div><A f={f} onF={setF} /><B f={f} /></div>;
}
const A = ({ f, onF }) => <i>{f}</i>;
const B = ({ f }) => <i>{f}</i>;
"""})
        bindings = analysis.components["App"].bindings
        assert bindings["f"].origin == "local-state" and bindings["f"].partner == "setF"
        assert bindings["setF"].origin == "setter" and bindings["setF"].partner == "f"
        findings = run_semantic_check(
            "react-shared-mutable-state", analysis, {"min_branches": 2})
        assert len(findings) == 1

    def test_hook_array_return(self):
        analysis = analyze_project({"App.tsx": """
function useToggle() {
  const [on, setOn] = useState(false);
  return [on, setOn];
}
function App() {
  const [open, setOpen] = useToggle();
  return <div>{open}</div>;
}
"""})
        bindings = analysis.components["App"].bindings
        assert bindings["open"].origin == "local-state"
        assert bindings["open"].partner == "setOpen"
        assert bindings["setOpen"].origin == "setter"

    def test_hook_wrapping_use_query_is_exempt(self):
        analysis = analyze_project({"App.tsx": """
function useUser() {
  const { data: user } = useQuery({ queryKey: ["u"] });
  return { user };
}
function App() {
  const { user } = useUser();
  return <Layout user={user} />;
}
const Layout = ({ user }) => <Mid user={user} />;
const Mid = ({ user }) => <Leaf user={user} />;
const Leaf = ({ user }) => <div>{user}</div>;
"""})
        assert analysis.components["App"].bindings["user"].origin == "query"
        assert run_semantic_check(
            "react-server-state-drilling", analysis, {"max_depth": 3}) == []

    def test_hook_forwarding_use_query_directly(self):
        analysis = analyze_project({"App.tsx": """
const useUser = () => useQuery({ queryKey: ["u"] });
function App() {
  const q = useUser();
  return <div>{q}</div>;
}
"""})
        assert analysis.components["App"].bindings["q"].origin == "query"

    def test_hook_calling_hook(self):
        analysis = analyze_project({"App.tsx": """
function useFetched() {
  const [data, setData] = useState(null);
  useEffect(() => { fetch("/d").then(setData); }, []);
  return { data };
}
function useReport() {
  const { data } = useFetched();
  return { report: data };
}
function App() {
  const { report } = useReport();
  return <div>{report}</div>;
}
"""})
        assert analysis.components["App"].bindings["report"].origin == "server-state"

    def test_setter_passed_into_fetching_hook_promotes_state(self):
        analysis = analyze_project({"App.tsx": """
function useLoad(set) {
  useEffect(() => { fetch("/d").then((r) => r.json()).then(set); }, []);
}
function App() {
  const [data, setData] = useState(null);
  useLoad(setData);
  return <Layout data={data} />;
}
const Layout = ({ data }) => <Mid data={data} />;
const Mid = ({ data }) => <Leaf data={data} />;
const Leaf = ({ data }) => <div>{data}</div>;
"""})
        assert analysis.components["App"].bindings["data"].origin == "server-state"
        findings = run_semantic_check(
            "react-server-state-drilling", analysis, {"max_depth": 3})
        assert len(findings) == 1

    def test_setter_forwarded_hook_to_hook_promotes(self):
        analysis = analyze_project({"App.tsx": """
function useFetchInto(write) {
  useEffect(() => { fetch("/d").then(write); }, []);
}
function useLoader(sink) {
  useFetchInto(sink);
}
function App() {
  const [rows, setRows] = useState([]);
  useLoader(setRows);
  return <div>{rows}</div>;
}
"""})
        assert analysis.components["App"].bindings["rows"].origin == "server-state"

    def test_unknown_hook_stays_local(self):
        analysis = analyze_project({"App.tsx": """
function App() {
  const { user } = useSomethingImported();
  return <div>{user}</div>;
}
"""})
        assert analysis.components["App"].bindings["user"].origin == "local"


# ------------------------------------------------------- engine integration


class TestEngineIntegration:
    def _semantic_rule(self, **params):
        return Rule(
            id="drill", language="tsx", type="semantic",
            check="react-prop-drilling", params=params or {"max_depth": 3},
            message="prop drilling")

    def test_validate_semantic_rule(self):
        validate_rule(self._semantic_rule())
        with pytest.raises(RuleError):
            validate_rule(Rule(id="x", language="tsx", type="semantic",
                               check="not-a-check", message="m"))
        with pytest.raises(RuleError):
            validate_rule(Rule(id="x", language="python", type="semantic",
                               check="react-prop-drilling", message="m"))

    def test_check_single_string_with_virtual_files(self):
        code = "\n".join(
            f"// @file: {path}\n{source}" for path, source in BAD_PROJECT.items())
        report = ENGINE.check(code, "tsx", [self._semantic_rule()])
        assert not report.passed
        assert {v.file for v in report.violations} == {"App.tsx"}
        assert "Sidebar" in report.violations[0].message

    def test_check_project_multi_file(self):
        report = ENGINE.check_project(BAD_PROJECT, "tsx", [self._semantic_rule()])
        assert len(report.violations) == 2
        assert report.violations[0].file == "App.tsx"

    def test_check_project_mixes_single_file_rules(self):
        no_var = Rule(id="ts-no-var", language="typescript", type="forbid",
                      query="(variable_declaration) @bad", capture="bad",
                      message="no var")
        files = dict(GOOD_PROJECT)
        files["legacy.ts"] = "var x = 1;\n"
        report = ENGINE.check_project(files, "tsx",
                                      [self._semantic_rule(), no_var])
        assert [v.rule_id for v in report.violations] == ["ts-no-var"]
        assert report.violations[0].file == "legacy.ts"

    def test_rule_roundtrips_params(self):
        rule = self._semantic_rule(max_depth=5, origins=["local-state"])
        again = Rule.from_dict(rule.to_dict())
        assert again.check == "react-prop-drilling"
        assert again.params == {"max_depth": 5, "origins": ["local-state"]}


# --------------------------------------------------------------- the store


class TestStoreIntegration:
    def test_seed_conventions_self_verify_on_import(self, store):
        added = store.import_file(REACT_JSON)
        assert added == [
            "react-server-state-drilling",
            "react-shared-mutable-state",
            "react-prop-drilling",
        ]

    def test_undemonstrated_semantic_rule_rejected(self, store):
        rule = Rule(id="r", language="tsx", type="semantic",
                    check="react-prop-drilling", params={"max_depth": 3},
                    message="m")
        conv = Convention(
            id="r", kind="rule", language="tsx", title="t", rule=rule,
            bad_example="const NotDrilling = () => <div />;\n")
        with pytest.raises(RuleError, match="does not flag"):
            store.add(conv)

    def test_seeded_rules_flag_a_real_project_string(self, store):
        store.import_file(REACT_JSON)
        from acode.agent import steps

        rules = steps.applicable_rules(store, "tsx", None)
        code = "\n".join(
            f"// @file: {path}\n{source}" for path, source in BAD_PROJECT.items())
        report = steps.check(code, "tsx", rules)
        assert {v.rule_id for v in report.violations} == {
            "react-server-state-drilling",
            "react-shared-mutable-state",
            "react-prop-drilling",
        }


# ----------------------------------------------------------- MCP + CLI


def _write_project(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")


class TestMcpCheckProject:
    @pytest.fixture()
    def server(self, store):
        from acode.config import AcodeConfig
        from acode.mcpserver.server import build_server

        store.import_file(REACT_JSON)
        config = AcodeConfig()
        config.db_path = ":memory:"
        return build_server(config, store)

    async def _call(self, server, tool, args):
        result, _ = await server.call_tool(tool, args)
        return json.loads(result[0].text)

    async def test_tool_reports_cross_file_violations(self, server, tmp_path):
        _write_project(tmp_path, BAD_PROJECT)
        out = await self._call(server, "check_project", {"path": str(tmp_path)})
        assert not out["passed"]
        rule_ids = {v["rule_id"] for v in out["violations"]}
        assert "react-server-state-drilling" in rule_ids
        assert out["files_checked"] == sorted(BAD_PROJECT)
        server_v = next(v for v in out["violations"]
                        if v["rule_id"] == "react-server-state-drilling")
        assert server_v["file"] == "App.tsx"
        assert "UserCard" in server_v["message"]

    async def test_tool_passes_good_project(self, server, tmp_path):
        _write_project(tmp_path, GOOD_PROJECT)
        out = await self._call(server, "check_project", {"path": str(tmp_path)})
        assert out["passed"]

    async def test_add_semantic_convention_over_mcp(self, server):
        out = await self._call(server, "add_convention", {
            "id": "my-drill", "language": "tsx", "title": "custom depth",
            "rule_type": "semantic", "check": "react-prop-drilling",
            "params": {"max_depth": 2}, "message": "too deep",
            "bad_example": BAD_PROJECT["App.tsx"] + BAD_PROJECT["Layout.tsx"].replace(
                "import Sidebar from \"./Sidebar\";",
                "const Sidebar = ({ user }) => <b>{user}</b>;"),
            "good_example": "const Flat = () => <div />;\n",
        })
        assert out["added"] == "my-drill" and out["self_verified"]


class TestCliCheckProject:
    def test_exit_codes_and_output(self, tmp_path, capsys):
        from acode.cli import main

        db = tmp_path / "db.sqlite"
        assert main(["--db", str(db), "import", str(REACT_JSON)]) == 0
        capsys.readouterr()

        bad_dir = tmp_path / "bad"
        _write_project(bad_dir, BAD_PROJECT)
        assert main(["--db", str(db), "check-project", str(bad_dir)]) == 1
        out = json.loads(capsys.readouterr().out)
        assert any(v["rule_id"] == "react-server-state-drilling"
                   for v in out["violations"])

        good_dir = tmp_path / "good"
        _write_project(good_dir, GOOD_PROJECT)
        assert main(["--db", str(db), "check-project", str(good_dir)]) == 0
