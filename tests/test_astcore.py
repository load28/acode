import pytest

from acode.astcore import (
    Rule,
    RuleEngine,
    RuleError,
    cosine,
    fingerprint_code,
    parse,
)
from acode.astcore.rules import validate_rule

ENGINE = RuleEngine()


class TestFingerprint:
    def test_deterministic(self):
        code = "def foo(a, b):\n    return a + b\n"
        assert fingerprint_code(code, "python") == fingerprint_code(code, "python")

    def test_identifiers_do_not_change_shape(self):
        a = fingerprint_code("def foo(x):\n    return x + 1\n", "python")
        b = fingerprint_code("def bar(y):\n    return y + 2\n", "python")
        assert cosine(a, b) == pytest.approx(1.0)

    def test_similar_shape_ranks_above_different_shape(self):
        query = fingerprint_code(
            "@app.get('/items')\nasync def list_items(db=Depends(get_db)):\n    return await db.fetch()\n",
            "python",
        )
        similar = fingerprint_code(
            "@app.post('/users')\nasync def create_user(db=Depends(get_db)):\n    return await db.insert()\n",
            "python",
        )
        different = fingerprint_code(
            "class Config:\n    DEBUG = False\n    NAME = 'x'\n",
            "python",
        )
        assert cosine(query, similar) > cosine(query, different)

    def test_normalized(self):
        vec = fingerprint_code("x = 1\n", "python")
        assert sum(v * v for v in vec) == pytest.approx(1.0)


class TestRuleEngine:
    def test_forbid(self):
        rule = Rule(id="no-print", language="python", type="forbid",
                    query='(call function: (identifier) @fn (#eq? @fn "print"))',
                    capture="fn", message="no print")
        report = ENGINE.check("print(1)\nx = 2\nprint(3)\n", "python", [rule])
        assert [v.start_line for v in report.violations] == [1, 3]
        assert ENGINE.check("x = 1\n", "python", [rule]).passed

    def test_require(self):
        rule = Rule(id="need-main", language="python", type="require",
                    query='(if_statement condition: (comparison_operator (identifier) @n (#eq? @n "__name__")))',
                    message="missing __main__ guard")
        assert not ENGINE.check("x = 1\n", "python", [rule]).passed
        ok = 'if __name__ == "__main__":\n    pass\n'
        assert ENGINE.check(ok, "python", [rule]).passed

    def test_require_in(self):
        rule = Rule(
            id="docstring", language="python", type="require_in",
            scope_query="(function_definition) @scope", capture="scope",
            query="(function_definition body: (block . (expression_statement (string))))",
            message="missing docstring",
        )
        bad = "def f():\n    return 1\n"
        good = 'def f():\n    """doc"""\n    return 1\n'
        assert not ENGINE.check(bad, "python", [rule]).passed
        assert ENGINE.check(good, "python", [rule]).passed

    def test_naming(self):
        rule = Rule(id="snake", language="python", type="naming",
                    query="(function_definition name: (identifier) @name)",
                    capture="name", regex="[a-z_][a-z0-9_]*",
                    message="snake_case required")
        report = ENGINE.check("def BadName():\n    pass\n", "python", [rule])
        assert len(report.violations) == 1
        assert "BadName" in report.violations[0].message

    def test_syntax_error_reported(self):
        report = ENGINE.check("def broken(:\n", "python", [])
        assert not report.syntax_ok
        assert not report.passed

    def test_deterministic_output(self):
        rule = Rule(id="no-print", language="python", type="forbid",
                    query='(call function: (identifier) @fn (#eq? @fn "print"))',
                    message="no print")
        code = "print(1)\nprint(2)\n"
        r1 = ENGINE.check(code, "python", [rule]).to_dict()
        r2 = ENGINE.check(code, "python", [rule]).to_dict()
        assert r1 == r2

    def test_invalid_query_rejected(self):
        rule = Rule(id="bad", language="python", type="forbid",
                    query="(nonexistent_node) @x", message="x")
        with pytest.raises(RuleError):
            validate_rule(rule)

    def test_naming_requires_regex(self):
        rule = Rule(id="bad", language="python", type="naming",
                    query="(identifier) @n", capture="n", message="x")
        with pytest.raises(RuleError):
            validate_rule(rule)

    def test_rules_for_other_language_skipped(self):
        rule = Rule(id="ts-rule", language="typescript", type="forbid",
                    query="(variable_declaration) @bad", message="no var")
        report = ENGINE.check("x = 1\n", "python", [rule])
        assert report.checked_rules == []
        assert report.passed


def test_parse_typescript():
    tree = parse("const x: number = 1;\n", "typescript")
    assert not tree.root_node.has_error
