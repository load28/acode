"""Benchmark acode's deterministic review path on a real codebase.

Usage:
    python benchmarks/bench_review_mech.py <repo-path> [glob]

Example (React/TSX):
    git clone --depth 1 https://github.com/excalidraw/excalidraw /tmp/excalidraw
    python benchmarks/bench_review_mech.py /tmp/excalidraw '*.tsx'

Measures, per source file:
  - check_code path: resolve_dialect + applicable_rules + rule engine check
  - retrieval: store.search(language, code=..., top_k=8)
against (A) a seed-conventions-only store and (B) the same store after
index_codebase() over the target repo (realistic in-project usage).
"""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

from acode.agent import steps
from acode.astcore.parser import resolve_dialect
from acode.rag.indexer import index_codebase
from acode.rag.store import ConventionStore

CONV_DIR = Path(__file__).resolve().parent.parent / "conventions"


def main() -> None:
    repo = Path(sys.argv[1])
    pattern = sys.argv[2] if len(sys.argv) > 2 else "*.tsx"
    files = sorted(p for p in repo.rglob(pattern) if "node_modules" not in p.parts)
    codes = [(p, p.read_text(encoding="utf-8", errors="replace")) for p in files]
    print(f"files: {len(codes)}, total lines: {sum(c.count(chr(10)) for _, c in codes)}")

    with tempfile.TemporaryDirectory() as tmp:
        store = ConventionStore(str(Path(tmp) / "bench.db"))
        for f in CONV_DIR.glob("*.json"):
            store.import_file(f, replace=True)
        print("conventions loaded:", len(store.list()))

        # warmup: grammar init + query compilation
        warm = codes[0][1]
        lang = resolve_dialect(warm, "typescript")
        rules = steps.applicable_rules(store, lang, None)
        steps.check(warm, lang, rules)
        print(f"rules enforced for {lang}: {len(rules)}")

        # phase 1: check_code path over every file
        results = []
        t_all0 = time.perf_counter()
        for p, code in codes:
            t0 = time.perf_counter()
            lang = resolve_dialect(code, "typescript")
            rls = steps.applicable_rules(store, lang, None)
            report = steps.check(code, lang, rls)
            results.append({
                "file": str(p.relative_to(repo)), "lines": code.count("\n"),
                "ms": (time.perf_counter() - t0) * 1000,
                "violations": len(report.violations), "syntax_ok": report.syntax_ok,
            })
        t_all = time.perf_counter() - t_all0

        ms = sorted(r["ms"] for r in results)

        def pct(q: float) -> float:
            return ms[min(len(ms) - 1, int(len(ms) * q))]

        print(f"\n== check_code path, N={len(ms)} ==")
        print(f"total: {t_all:.2f}s  mean: {statistics.mean(ms):.1f}ms  "
              f"median: {pct(.5):.1f}ms  p90: {pct(.9):.1f}ms  "
              f"p99: {pct(.99):.1f}ms  max: {max(ms):.1f}ms")
        tot_lines = sum(r["lines"] for r in results)
        print(f"throughput: {tot_lines / t_all:,.0f} lines/s   "
              f"violations: {sum(r['violations'] for r in results)}")
        for r in sorted(results, key=lambda r: -r["ms"])[:5]:
            print(f"  slowest: {r['ms']:7.1f}ms  {r['lines']:6d} lines  "
                  f"viol={r['violations']:4d}  {r['file']}")

        # phase 2: retrieval latency, seed-only vs indexed store
        sample = [codes[i] for i in range(0, len(codes), max(1, len(codes) // 50))]

        def bench_retrieval(label: str) -> None:
            lat = []
            for _, code in sample:
                t0 = time.perf_counter()
                store.search(language="tsx", code=code, top_k=8)
                lat.append((time.perf_counter() - t0) * 1000)
            lat.sort()
            print(f"== retrieval, {label} ({len(store.list())} conventions), "
                  f"N={len(lat)} ==")
            print(f"mean: {statistics.mean(lat):.1f}ms  "
                  f"median: {lat[len(lat) // 2]:.1f}ms  max: {max(lat):.1f}ms")

        print()
        bench_retrieval("seed-only store")
        t0 = time.perf_counter()
        idx = index_codebase(store, str(repo), metadata={})
        print(f"\n== index_codebase: {idx['files']} files, "
              f"{len(idx['indexed'])} patterns in {time.perf_counter() - t0:.1f}s ==")
        bench_retrieval("indexed store")

    out = Path(__file__).parent / "bench_mech_results.json"
    out.write_text(json.dumps(results))
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
