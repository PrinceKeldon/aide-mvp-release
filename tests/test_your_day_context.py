from datetime import date

from daily_brief.context import _gather_from_memory, _gather_recent_context


class FakeMemory:
    def search(self, query, n=5):
        assert n == 3
        return [{"content": f"note for {query}"}]

    def get_recent(self, n=10):
        assert n == 5
        return [
            {
                "timestamp": "2026-04-02T09:00:00",
                "user": "What is on my calendar?",
                "assistant": "You have a lunch at noon.",
            }
        ]


def test_gather_from_memory_uses_current_search_signature(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "memory.manager", type("M", (), {"MemoryManager": FakeMemory}))
    notes = _gather_from_memory(date(2026, 4, 2))
    assert notes
    assert "important tasks today" in notes[0]


def test_gather_recent_context_uses_current_get_recent_signature(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "memory.manager", type("M", (), {"MemoryManager": FakeMemory}))
    context = _gather_recent_context()
    assert context == [
        "User: What is on my calendar?",
        "VERA: You have a lunch at noon.",
    ]
