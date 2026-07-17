"""Export conventions/*.json into the official-site data file.

For every ``rule`` entry the bad/good examples are checked against the real
RuleEngine at export time, and the resulting violations are embedded in the
output — the site shows exactly what the engine suggests, never a hand-written
approximation. The export fails if a bad example produces no violation or a
good example produces one, so a docs/engine mismatch breaks the build instead
of shipping.

Usage: python scripts/export_site_data.py [output-path]
Default output: site/src/data/rules.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from acode.astcore.rules import Rule, RuleEngine

REPO_ROOT = Path(__file__).resolve().parent.parent
CONVENTIONS_DIR = REPO_ROOT / "conventions"
DEFAULT_OUTPUT = REPO_ROOT / "site" / "src" / "data" / "rules.json"


def check_example(engine: RuleEngine, code: str, language: str, rule: Rule) -> list[dict]:
    report = engine.check(code, language, [rule])
    return [v.to_dict() for v in report.violations]


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    engine = RuleEngine()
    categories: dict[str, list[dict]] = defaultdict(list)
    errors: list[str] = []
    n_rules = n_patterns = 0

    for path in sorted(CONVENTIONS_DIR.glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8")):
            language = entry["language"]
            category = entry["metadata"].get("category", "uncategorized")
            item = {
                "id": entry["id"],
                "kind": entry["kind"],
                "language": language,
                "title": entry["title"],
                "guideline": entry["guideline"],
                "tags": entry["metadata"].get("tags", []),
                "good_example": entry.get("good_example"),
                "bad_example": entry.get("bad_example"),
                "rule_type": None,
                "engine_message": None,
                "violations": [],
            }
            if entry["kind"] == "rule":
                n_rules += 1
                rule = Rule.from_dict(entry["rule"])
                item["rule_type"] = rule.type
                item["engine_message"] = rule.message
                bad = entry.get("bad_example")
                if bad:
                    item["violations"] = check_example(engine, bad, language, rule)
                    if not item["violations"]:
                        errors.append(f"{entry['id']}: bad example produces no violation")
                good = entry.get("good_example")
                if good:
                    leaks = check_example(engine, good, language, rule)
                    if leaks:
                        errors.append(f"{entry['id']}: good example flagged: {leaks}")
            else:
                n_patterns += 1
            categories[category].append(item)

    if errors:
        for err in errors:
            print(f"ERROR {err}", file=sys.stderr)
        return 1

    ordered = sorted(categories.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    data = {
        "categories": [
            {
                "slug": slug,
                "count": len(items),
                "languages": sorted({i["language"] for i in items}),
                "entries": items,
            }
            for slug, items in ordered
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {output}: {n_rules + n_patterns} entries "
        f"({n_rules} rules, {n_patterns} patterns), {len(categories)} categories"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
