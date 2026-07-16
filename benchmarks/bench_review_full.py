"""Full review_code benchmark (LLM included) on real source files.

Usage:
    python benchmarks/bench_review_full.py <repo-path> [glob]

Picks two representative files (closest to 110 and 680 lines — the median
and p90 of a typical React repo) and runs CodingPipeline.review twice on
each, separating LLM wall time from the deterministic remainder. Requires
a working LLM provider (claude CLI or API key).
"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

from acode.agent.pipeline import CodingPipeline
from acode.config import AcodeConfig
from acode.llm.factory import create_provider
from acode.rag.store import ConventionStore

CONV_DIR = Path(__file__).resolve().parent.parent / "conventions"


class TimedProvider:
    """Wraps a provider to record per-call wall time."""

    def __init__(self, inner):
        self.inner, self.name, self.calls = inner, inner.name, []

    async def complete(self, system: str, prompt: str) -> str:
        t0 = time.perf_counter()
        reply = await self.inner.complete(system, prompt)
        self.calls.append({"s": time.perf_counter() - t0,
                           "prompt_chars": len(prompt),
                           "reply_chars": len(reply)})
        return reply


async def run(repo: Path, pattern: str) -> None:
    files = sorted(p for p in repo.rglob(pattern) if "node_modules" not in p.parts)
    sized = sorted((p.read_text(errors="replace").count("\n"), p) for p in files)
    targets = [min(sized, key=lambda t: abs(t[0] - n)) for n in (110, 680)]
    config = AcodeConfig()

    with tempfile.TemporaryDirectory() as tmp:
        store = ConventionStore(str(Path(tmp) / "bench.db"))
        for f in CONV_DIR.glob("*.json"):
            store.import_file(f, replace=True)

        for run_no in (1, 2):
            for lines, path in targets:
                provider = TimedProvider(create_provider(config))
                pipe = CodingPipeline(store, provider, config)
                t0 = time.perf_counter()
                res = await pipe.review(code=path.read_text(errors="replace"),
                                        language="typescript")
                total = time.perf_counter() - t0
                llm = sum(c["s"] for c in provider.calls)
                c = provider.calls[0]
                print(f"run{run_no} {path.relative_to(repo)} ({lines} lines): "
                      f"total={total:.1f}s  llm={llm:.1f}s ({llm / total * 100:.0f}%)  "
                      f"mech={total - llm:.2f}s  prompt={c['prompt_chars']:,}ch  "
                      f"reply={c['reply_chars']:,}ch  "
                      f"violations={len(res.violations)}  "
                      f"fix={'y' if res.suggested_fix else 'n'}  "
                      f"fix_verified={res.fix_verified}")


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]),
                    sys.argv[2] if len(sys.argv) > 2 else "*.tsx"))
