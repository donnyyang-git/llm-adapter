from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from llm_adapter.conversation_service import (
    ConversationService,
    QuestionSendError,
    TabBusyError,
)
from llm_adapter.event_broker import SessionEventBroker
from llm_adapter.models import TabInfo
from llm_adapter.storage import ConversationStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 20, 6, 30, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


def gemini_tab() -> TabInfo:
    return TabInfo(
        id="TARGET-123",
        title="Gemini conversation",
        url="https://gemini.google.com/app/abc",
        is_gemini=True,
        is_selected=True,
    )


@pytest.mark.asyncio
async def test_saves_pending_before_sending_and_calls_sender_once(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    observed_statuses: list[str] = []
    session_id = ""

    async def sender(_: str, __: str) -> None:
        observed_statuses.append(store.load(session_id).turns[-1].status)

    service = ConversationService(store, sender, now=Clock())
    session = await service.create_session(gemini_tab())
    session_id = session.session_id

    result = await service.begin_turn(session_id, "Question")

    assert observed_statuses == ["pending"]
    assert result.turns[-1].status == "generating"


@pytest.mark.asyncio
async def test_rejects_second_turn_while_tab_is_busy(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    service = ConversationService(ConversationStore(tmp_path), sender, now=Clock())
    session = await service.create_session(gemini_tab())
    await service.begin_turn(session.session_id, "First")

    with pytest.raises(TabBusyError):
        await service.begin_turn(session.session_id, "Second")

    completed = await service.complete_turn(session.session_id, "Answer", "**Answer**")
    assert completed.turns[-1].status == "completed"


@pytest.mark.asyncio
async def test_send_failure_is_saved_without_retrying(tmp_path: Path) -> None:
    calls = 0

    async def sender(_: str, __: str) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("Send state is uncertain")

    store = ConversationStore(tmp_path)
    service = ConversationService(store, sender, now=Clock())
    session = await service.create_session(gemini_tab())

    with pytest.raises(QuestionSendError, match="not sent again"):
        await service.begin_turn(session.session_id, "Question")

    saved = store.load(session.session_id)
    assert calls == 1
    assert saved.turns[-1].status == "failed"
    assert saved.turns[-1].error == "Send state is uncertain"


@pytest.mark.asyncio
async def test_persisted_active_turn_blocks_resend_after_restart(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    store = ConversationStore(tmp_path)
    first_service = ConversationService(store, sender, now=Clock())
    session = await first_service.create_session(gemini_tab())
    await first_service.begin_turn(session.session_id, "First")
    restarted_service = ConversationService(store, sender, now=Clock())

    with pytest.raises(TabBusyError):
        await restarted_service.begin_turn(session.session_id, "Duplicate")


@pytest.mark.asyncio
async def test_partial_response_is_saved_and_releases_tab(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    store = ConversationStore(tmp_path)
    service = ConversationService(store, sender, now=Clock())
    session = await service.create_session(gemini_tab())
    await service.begin_turn(session.session_id, "First")
    await service.update_response(session.session_id, "Partial", "**Partial**")

    partial = await service.partial_turn(
        session.session_id,
        "Partial",
        "**Partial**",
        "Timed out",
    )
    next_turn = await service.begin_turn(session.session_id, "Second")

    assert partial.turns[-1].status == "partial"
    assert partial.turns[-1].response_text == "Partial"
    assert next_turn.turns[-1].turn_id == "turn-2"


@pytest.mark.asyncio
async def test_failure_preserves_already_captured_response(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    service = ConversationService(ConversationStore(tmp_path), sender, now=Clock())
    session = await service.create_session(gemini_tab())
    await service.begin_turn(session.session_id, "Question")
    await service.update_response(session.session_id, "Partial", "**Partial**")

    failed = await service.fail_turn(session.session_id, "Tab was closed")

    assert failed.turns[-1].status == "failed"
    assert failed.turns[-1].response_text == "Partial"
    assert failed.turns[-1].response_markdown == "**Partial**"


@pytest.mark.asyncio
async def test_publishes_turn_events_after_persisting_each_state(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    broker = SessionEventBroker()
    service = ConversationService(
        ConversationStore(tmp_path), sender, now=Clock(), event_broker=broker
    )
    session = await service.create_session(gemini_tab())

    async with broker.subscribe(session.session_id) as events:
        await service.begin_turn(session.session_id, "Question")
        await service.update_response(session.session_id, "Partial", "**Partial**")
        await service.complete_turn(session.session_id, "Answer", "**Answer**")

        received = [await events.get() for _ in range(5)]

    assert [event.event for event in received] == [
        "queued",
        "sent",
        "generating",
        "response_update",
        "completed",
    ]
    assert all(event.session_id == session.session_id for event in received)
    assert all(event.turn_id == "turn-1" for event in received)
    assert received[-1].data["response_text"] == "Answer"


@pytest.mark.asyncio
async def test_restart_can_resume_persisted_active_turn_without_resending(
    tmp_path: Path,
) -> None:
    sends = 0

    async def sender(_: str, __: str) -> None:
        nonlocal sends
        sends += 1

    store = ConversationStore(tmp_path)
    first_service = ConversationService(store, sender, now=Clock())
    session = await first_service.create_session(gemini_tab())
    await first_service.begin_turn(session.session_id, "Question")
    restarted_service = ConversationService(store, sender, now=Clock())

    resumed = await restarted_service.resume_turn(session.session_id)
    completed = await restarted_service.complete_turn(
        session.session_id, "Recovered", "Recovered"
    )

    assert sends == 1
    assert resumed.turns[-1].status == "generating"
    assert completed.turns[-1].status == "completed"