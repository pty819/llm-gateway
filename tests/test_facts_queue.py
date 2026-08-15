from contextlib import asynccontextmanager

import llm_gateway.services.facts_queue as facts_queue


class _DummySavepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def commit(self):
        return None

    def begin_nested(self):
        return _DummySavepoint()


async def test_drain_isolates_a_failing_fact_and_keeps_the_rest(monkeypatch):
    attempted: list[str] = []

    @asynccontextmanager
    async def fake_session_local():
        yield _DummySession()

    async def fake_record(_session, **kwargs):
        attempted.append(kwargs["endpoint"])
        if kwargs["endpoint"] == "bad":
            raise RuntimeError("boom")

    monkeypatch.setattr("llm_gateway.db.session.AsyncSessionLocal", fake_session_local)
    monkeypatch.setattr(
        "llm_gateway.services.facts.record_request_fact", fake_record
    )
    facts_queue._pending.clear()
    facts_queue._draining = False

    for endpoint in ("good1", "bad", "good2"):
        await facts_queue.enqueue_fact({"endpoint": endpoint}, endpoint)
    await facts_queue.drain_now(timeout_seconds=5.0)

    # The failing fact must not poison the batch: all three are attempted and
    # the queue is fully drained.
    assert sorted(attempted) == ["bad", "good1", "good2"]
    assert facts_queue._pending == []
    assert facts_queue._draining is False


async def test_drain_now_on_empty_queue_returns_immediately():
    facts_queue._pending.clear()
    facts_queue._draining = False
    await facts_queue.drain_now(timeout_seconds=1.0)
    assert facts_queue._pending == []
