from pathlib import Path

import pytest

from acode.rag.store import ConventionStore

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def store() -> ConventionStore:
    s = ConventionStore(":memory:")
    yield s
    s.close()


@pytest.fixture()
def seeded_store(store: ConventionStore) -> ConventionStore:
    store.import_file(REPO_ROOT / "conventions" / "python.json")
    store.import_file(REPO_ROOT / "conventions" / "typescript.json")
    return store


from acode.llm.base import LlmProvider


class FakeProvider(LlmProvider):
    """Scripted LLM: returns queued replies in order."""

    name = "fake"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        if not self.replies:
            raise AssertionError("FakeProvider ran out of scripted replies")
        return self.replies.pop(0)


@pytest.fixture()
def fake_provider_factory():
    return FakeProvider
