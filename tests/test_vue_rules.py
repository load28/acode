"""Vue 3 semantic rules: SFC handling, template scanner, script extraction,
composables, chains, checks, and store/engine integration.

Mirrors test_react_rules.py — same cross-context verdicts (prop-drilling
depth + data origin), Vue syntax: <script setup>, defineProps, ref,
onMounted+fetch, v-model/@event mutation edges, provide/inject.
"""

import json

import pytest

from acode.astcore.flow import run_semantic_check, semantic_check_names
from acode.astcore.parser import language_for_path, parse
from acode.astcore.rules import Rule, RuleEngine, RuleError, validate_rule
from acode.astcore.vue import (
    analyze_project,
    analyze_source,
    extract_vue_file_facts,
    scan_template_tags,
    script_only_view,
    split_sfc,
)

from tests.conftest import REPO_ROOT

ENGINE = RuleEngine()

VUE_JSON = REPO_ROOT / "conventions" / "vue.json"


BAD_PROJECT = {
    "App.vue": """
<script setup>
import { ref, onMounted } from "vue";
import Layout from "./Layout.vue";
import Toolbar from "./Toolbar.vue";

const user = ref(null);
const filter = ref("");
onMounted(() => {
  fetch("/api/user").then((r) => r.json()).then((d) => { user.value = d; });
});
</script>
<template>
  <div>
    <Toolbar v-model="filter" />
    <Layout :user="user" :filter="filter" />
  </div>
</template>
""",
    "Toolbar.vue": """
<script setup>
defineProps(["modelValue"]);
</script>
<template>
  <input :value="modelValue" />
</template>
""",
    "Layout.vue": """
<script setup>
import Sidebar from "./Sidebar.vue";
const props = defineProps<{ user: object; filter: string }>();
</script>
<template>
  <Sidebar :user="user" :filter="filter" />
</template>
""",
    "Sidebar.vue": """
<script setup>
import UserCard from "./UserCard.vue";
defineProps(["user", "filter"]);
</script>
<template>
  <user-card :user="user" :label="filter" />
</template>
""",
    "UserCard.vue": """
<script setup>
defineProps(["user", "label"]);
</script>
<template>
  <div>{{ user?.name }}{{ label }}</div>
</template>
""",
}

GOOD_PROJECT = {
    "App.vue": """
<script setup>
import { ref, provide } from "vue";
import Layout from "./Layout.vue";
const filter = ref("");
provide("filter", filter);
</script>
<template>
  <Layout />
</template>
""",
    "Layout.vue": """
<script setup>
import UserCard from "./UserCard.vue";
</script>
<template>
  <UserCard />
</template>
""",
    "UserCard.vue": """
<script setup>
import { inject } from "vue";
import { useQuery } from "@tanstack/vue-query";
const filter = inject("filter");
const { data: user } = useQuery({ queryKey: ["user"] });
</script>
<template>
  <div>{{ user?.name }}{{ filter }}</div>
</template>
""",
}


@pytest.fixture()
def bad_analysis():
    return analyze_project(BAD_PROJECT)


# ------------------------------------------------------------- SFC layout


class TestSfcLayout:
    def test_script_view_preserves_positions(self):
        code = "<template>\n  <div />\n</template>\n<script setup>\nconst x = 1;\n</script>\n"
        view = script_only_view(code)
        assert view.count("\n") == code.count("\n")
        assert view.splitlines()[4] == "const x = 1;"
        assert set(view.splitlines()[1]) <= {" "}  # template line blanked

    def test_split_sfc_template_offset(self):
        code = "<script setup>\nconst x = 1;\n</script>\n<template>\n  <Child :a=\"x\" />\n</template>\n"
        _, template, offset = split_sfc(code)
        assert "<Child" in template
        assert offset == 3  # lines before the template content

    def test_vue_language_registered(self):
        assert language_for_path("App.vue") == "vue"
        tree = parse("<template><p>x</p></template>\n<script setup>\nvar x = 1;\n</script>\n", "vue")
        assert not tree.root_node.has_error


# -------------------------------------------------------- template scanner


class TestTemplateScanner:
    def test_tags_attrs_and_positions(self):
        tags = scan_template_tags(
            '\n  <UserCard :user="user"\n    @save="onSave" />\n')
        assert len(tags) == 1
        tag = tags[0]
        assert tag.name == "UserCard" and tag.line == 2
        assert [(a.name, a.value) for a in tag.attrs] == [
            (":user", "user"), ("@save", "onSave")]
        assert tag.attrs[1].line == 3

    def test_quote_aware_gt_inside_value(self):
        tags = scan_template_tags('<Item :label="a > b ? a : b" />')
        assert tags[0].attrs[0].value == "a > b ? a : b"

    def test_comments_and_closing_tags_skipped(self):
        tags = scan_template_tags(
            "<!-- <Ghost :x=\"y\" /> -->\n<div><Real :a=\"b\" /></div>\n")
        assert [t.name for t in tags] == ["div", "Real"]


# ------------------------------------------------------ script extraction


class TestScriptExtraction:
    def _facts(self, sfc: str):
        return extract_vue_file_facts("X.vue", sfc)

    def test_define_props_type_argument(self):
        comp = self._facts("""
<script setup>
const props = defineProps<{ user: object; count?: number }>();
</script>
<template><div /></template>
""").components["X"]
        assert comp.props == ["user", "count"]
        assert comp.props_param == "props"

    def test_define_props_interface_reference(self):
        comp = self._facts("""
<script setup>
interface Props { title: string; id: number }
const props = defineProps<Props>();
</script>
<template><div /></template>
""").components["X"]
        assert comp.props == ["title", "id"]

    def test_define_props_object_array_and_bare(self):
        comp = self._facts("""
<script setup>
defineProps({ name: String, age: Number });
</script>
<template><div /></template>
""").components["X"]
        assert comp.props == ["name", "age"]

    def test_with_defaults_unwrapped(self):
        comp = self._facts("""
<script setup>
const props = withDefaults(defineProps<{ size: number }>(), { size: 1 });
</script>
<template><div /></template>
""").components["X"]
        assert comp.props == ["size"]

    def test_ref_is_local_state_and_computed_derives(self):
        comp = analyze_project({"X.vue": """
<script setup>
import { ref, computed } from "vue";
const items = ref([]);
const total = computed(() => items.value.length);
</script>
<template><div /></template>
"""}).components["X"]
        assert comp.bindings["items"].origin == "local-state"
        assert comp.bindings["total"].origin == "local-state"  # inherited
        assert comp.bindings["total"].origin_root == "items"

    def test_fetch_assignment_in_on_mounted_promotes(self):
        comp = self._facts("""
<script setup>
import { ref, onMounted } from "vue";
const user = ref(null);
onMounted(async () => {
  const r = await fetch("/api");
  user.value = await r.json();
});
</script>
<template><div /></template>
""").components["X"]
        assert comp.bindings["user"].origin == "server-state"

    def test_fetch_input_ref_not_promoted(self):
        # user is an INPUT to the fetch, not written by it
        comp = self._facts("""
<script setup>
import { ref, watch } from "vue";
const user = ref("a");
const log = ref(null);
watch(user, () => {
  fetch("/log/" + user.value).then((r) => { log.value = r; });
});
</script>
<template><div /></template>
""").components["X"]
        assert comp.bindings["user"].origin == "local-state"
        assert comp.bindings["log"].origin == "server-state"

    def test_top_level_fetch_then_promotes(self):
        comp = self._facts("""
<script setup>
import { ref } from "vue";
const rows = ref([]);
fetch("/rows").then((r) => r.json()).then((d) => { rows.value = d; });
</script>
<template><div /></template>
""").components["X"]
        assert comp.bindings["rows"].origin == "server-state"

    def test_use_query_and_inject_origins(self):
        comp = self._facts("""
<script setup>
import { inject } from "vue";
import { useQuery } from "@tanstack/vue-query";
const { data: user } = useQuery({ queryKey: ["u"] });
const theme = inject("theme");
</script>
<template><div /></template>
""").components["X"]
        assert comp.bindings["user"].origin == "query"
        assert comp.bindings["theme"].origin == "context"

    def test_to_refs_of_props(self):
        comp = self._facts("""
<script setup>
import { toRefs } from "vue";
const props = defineProps<{ user: object }>();
const { user } = toRefs(props);
</script>
<template><Child :u="user" /></template>
""").components["X"]
        assert comp.bindings["user"].origin == "prop"


# --------------------------------------------------------- chains + rules


class TestVueChains:
    def test_cross_file_chain_with_kebab_tag(self, bad_analysis):
        chain = max((c for c in bad_analysis.chains if c.source == "user"),
                    key=lambda c: c.depth)
        assert chain.origin == "server-state"
        assert chain.depth == 3
        assert chain.path() == (
            "App -[user]-> Layout -[user]-> Sidebar -[user]-> UserCard")

    def test_rename_along_chain(self, bad_analysis):
        chain = max((c for c in bad_analysis.chains if c.source == "filter"),
                    key=lambda c: c.depth)
        assert chain.hops[-1].prop == "label"

    def test_server_state_drilling_fires(self, bad_analysis):
        findings = run_semantic_check(
            "vue-server-state-drilling", bad_analysis, {"max_depth": 3})
        assert len(findings) == 1
        assert findings[0].file == "App.vue"
        assert "UserCard" in findings[0].detail

    def test_shared_mutable_v_model_fan_out(self, bad_analysis):
        findings = run_semantic_check(
            "vue-shared-mutable-state", bad_analysis, {"min_branches": 2})
        assert len(findings) == 1
        assert "'filter'" in findings[0].detail
        assert "Toolbar" in findings[0].detail

    def test_event_assignment_counts_as_mutation(self):
        analysis = analyze_project({
            "App.vue": """
<script setup>
import { ref } from "vue";
import Editor from "./Editor.vue";
import View from "./View.vue";
const note = ref("");
</script>
<template>
  <div>
    <Editor :note="note" @change="note = $event" />
    <View :note="note" />
  </div>
</template>
""",
            "Editor.vue": "<script setup>defineProps(['note']);</script>\n<template><textarea /></template>\n",
            "View.vue": "<script setup>defineProps(['note']);</script>\n<template><p>{{ note }}</p></template>\n",
        })
        findings = run_semantic_check(
            "vue-shared-mutable-state", analysis, {"min_branches": 2})
        assert len(findings) == 1

    def test_mutating_function_handler_counts(self):
        analysis = analyze_project({"App.vue": """
<script setup>
import { ref } from "vue";
import Editor from "./Editor.vue";
import View from "./View.vue";
const note = ref("");
function onChange(v) { note.value = v; }
</script>
<template>
  <div>
    <Editor :note="note" @change="onChange" />
    <View :note="note" />
  </div>
</template>
"""})
        findings = run_semantic_check(
            "vue-shared-mutable-state", analysis, {"min_branches": 2})
        assert len(findings) == 1

    def test_provide_inject_is_exempt(self):
        analysis = analyze_project(GOOD_PROJECT)
        for name in ("vue-server-state-drilling", "vue-shared-mutable-state",
                     "vue-prop-drilling"):
            assert run_semantic_check(name, analysis, {}) == []

    def test_host_v_model_is_not_a_mutation_edge(self):
        facts = extract_vue_file_facts("X.vue", """
<script setup>
import { ref } from "vue";
const text = ref("");
</script>
<template><input v-model="text" /></template>
""")
        assert facts.components["X"].mutation_edges == []

    def test_deterministic(self):
        a = analyze_project(BAD_PROJECT)
        b = analyze_project(dict(reversed(list(BAD_PROJECT.items()))))
        assert [c.path() for c in a.chains] == [c.path() for c in b.chains]


# ------------------------------------------------------------ composables


class TestComposables:
    def test_composable_wrapping_fetch(self):
        analysis = analyze_project({
            "useUser.ts": """
import { ref, onMounted } from "vue";
export function useUser() {
  const user = ref(null);
  onMounted(() => {
    fetch("/u").then((r) => r.json()).then((d) => { user.value = d; });
  });
  return { user };
}
""",
            "App.vue": """
<script setup>
import { useUser } from "./useUser";
import Layout from "./Layout.vue";
const { user } = useUser();
</script>
<template><Layout :user="user" /></template>
""",
            "Layout.vue": "<script setup>import Mid from './Mid.vue';\ndefineProps(['user']);</script>\n<template><Mid :user=\"user\" /></template>\n",
            "Mid.vue": "<script setup>import Leaf from './Leaf.vue';\ndefineProps(['user']);</script>\n<template><Leaf :user=\"user\" /></template>\n",
            "Leaf.vue": "<script setup>defineProps(['user']);</script>\n<template><div>{{ user }}</div></template>\n",
        })
        assert analysis.components["App"].bindings["user"].origin == "server-state"
        findings = run_semantic_check(
            "vue-server-state-drilling", analysis, {"max_depth": 3})
        assert len(findings) == 1

    def test_composable_forwarding_use_query_is_exempt(self):
        analysis = analyze_project({
            "useUser.ts": "export const useUser = () => useQuery({ queryKey: ['u'] });\n",
            "App.vue": """
<script setup>
import { useUser } from "./useUser";
const q = useUser();
</script>
<template><div>{{ q }}</div></template>
""",
        })
        assert analysis.components["App"].bindings["q"].origin == "query"

    def test_ref_passed_into_fetching_composable_promotes(self):
        analysis = analyze_project({"App.vue": """
<script setup>
import { ref, onMounted } from "vue";
import Layout from "./Layout.vue";

function useLoad(target) {
  onMounted(() => {
    fetch("/d").then((r) => r.json()).then((d) => { target.value = d; });
  });
}
const data = ref(null);
useLoad(data);
</script>
<template><Layout :data="data" /></template>
"""})
        assert analysis.components["App"].bindings["data"].origin == "server-state"


# --------------------------------------------------- engine + store + CLI


class TestVueIntegration:
    def _rule(self):
        return Rule(id="v-drill", language="vue", type="semantic",
                    check="vue-prop-drilling", params={"max_depth": 3},
                    message="drilled")

    def test_validate_vue_semantic_rule(self):
        validate_rule(self._rule())
        assert {"vue-prop-drilling", "vue-server-state-drilling",
                "vue-shared-mutable-state"} <= set(semantic_check_names())

    def test_engine_single_string_virtual_sfcs(self):
        code = "\n".join(
            f"// @file: {path}\n{source}" for path, source in BAD_PROJECT.items())
        report = ENGINE.check(code, "vue", [self._rule()])
        assert not report.passed
        assert report.violations[0].file == "App.vue"

    def test_check_project_mixed_react_and_vue(self):
        react_rule = Rule(id="r-drill", language="tsx", type="semantic",
                          check="react-prop-drilling", params={"max_depth": 1},
                          message="react drilled")
        files = dict(BAD_PROJECT)
        files["Widget.tsx"] = (
            "function Widget() {\n"
            "  const [v, setV] = useState(1);\n"
            "  return <Leaf v={v} />;\n"
            "}\n"
            "const Leaf = ({ v }) => <i>{v}</i>;\n")
        report = ENGINE.check_project(files, "tsx", [self._rule(), react_rule])
        rule_ids = {v.rule_id for v in report.violations}
        assert "v-drill" in rule_ids and "r-drill" in rule_ids

    def test_ts_structural_rules_apply_inside_sfc_script(self):
        no_var = Rule(id="ts-no-var", language="typescript", type="forbid",
                      query="(variable_declaration) @bad", capture="bad",
                      message="no var")
        sfc = "<template>\n  <div />\n</template>\n<script setup>\nvar legacy = 1;\n</script>\n"
        report = ENGINE.check_project({"App.vue": sfc}, "vue", [no_var])
        assert len(report.violations) == 1
        assert report.violations[0].start_line == 5  # file line, not script line

    def test_seed_conventions_self_verify(self, store):
        added = store.import_file(VUE_JSON)
        assert added == ["vue-server-state-drilling",
                         "vue-shared-mutable-state", "vue-prop-drilling"]

    def test_cli_check_project(self, tmp_path, capsys):
        from acode.cli import main

        db = tmp_path / "db.sqlite"
        assert main(["--db", str(db), "import", str(VUE_JSON)]) == 0
        capsys.readouterr()

        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        for name, source in BAD_PROJECT.items():
            (bad_dir / name).write_text(source, encoding="utf-8")
        assert main(["--db", str(db), "check-project", str(bad_dir),
                     "--language", "vue"]) == 1
        out = json.loads(capsys.readouterr().out)
        rule_ids = {v["rule_id"] for v in out["violations"]}
        assert "vue-server-state-drilling" in rule_ids
        assert "vue-shared-mutable-state" in rule_ids
