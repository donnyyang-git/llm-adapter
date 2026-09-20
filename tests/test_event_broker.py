import pytest

from llm_adapter.event_broker import SessionEvent, SessionEventBroker


@pytest.mark.asyncio
async def test_publishes_only_to_matching_session_subscribers() -> None:
    broker = SessionEventBroker()
    event = SessionEvent(
        event="generating",
        session_id="session-1",
        turn_id="turn-1",
        data={"status": "generating"},
    )

    async with broker.subscribe("session-1") as matching, broker.subscribe(
        "session-2"
    ) as other:
        await broker.publish(event)

        assert await matching.get() == event
        assert other.empty()


@pytest.mark.asyncio
async def test_drops_oldest_event_for_slow_subscriber() -> None:
    broker = SessionEventBroker(queue_size=1)

    async with broker.subscribe("session-1") as queue:
        await broker.publish(SessionEvent(event="queued", session_id="session-1"))
        latest = SessionEvent(event="sent", session_id="session-1")
        await broker.publish(latest)

        assert await queue.get() == latest