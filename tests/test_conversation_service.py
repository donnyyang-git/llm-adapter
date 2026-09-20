from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from llm_adapter.conversation_service import (
    ConversationService,
    QuestionSendError,
    SessionRebindError,
    TabBusyError,
)
from llm_adapter.event_broker import SessionEventBroker
from llm_adapter.models import ResponseArtifact, TabInfo
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
async def test_update_response_persists_preview_image(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    store = ConversationStore(tmp_path)
    service = ConversationService(store, sender, now=Clock())
    session = await service.create_session(gemini_tab())
    await service.begin_turn(session.session_id, "Question")

    updated = await service.update_response(
        session.session_id,
        "Preview",
        "**Preview**",
        b"png-bytes",
    )

    saved = store.load(session.session_id)

    assert updated.turns[-1].response_image_path.startswith(
        f"/artifacts/{session.session_id}/"
    )
    assert saved.turns[-1].response_image_path.endswith("/turn-1.png")
    assert (tmp_path / "_artifacts" / session.session_id / "turn-1.png").read_bytes() == b"png-bytes"


@pytest.mark.asyncio
async def test_update_response_persists_multiple_artifacts(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    store = ConversationStore(tmp_path)
    service = ConversationService(store, sender, now=Clock())
    session = await service.create_session(gemini_tab())
    await service.begin_turn(session.session_id, "Question")

    updated = await service.update_response(
        session.session_id,
        "Summary",
        "Summary\n\n```python\nprint('a')\n```",
        response_artifacts=[
            ResponseArtifact(
                artifact_id="artifact-1",
                title="Code snippet 1",
                kind="code-block",
                language="python",
                code="print('a')",
                preview_type="code",
            ).model_dump(),
            ResponseArtifact(
                artifact_id="artifact-2",
                title="Code snippet 2",
                kind="code-block",
                language="sql",
                code="select 1;",
                preview_type="code",
            ).model_dump(),
        ],
    )

    saved = store.load(session.session_id)

    assert len(updated.turns[-1].response_artifacts) == 2
    assert updated.turns[-1].response_artifacts[1].language == "sql"
    assert len(saved.turns[-1].response_artifacts) == 2
    assert saved.turns[-1].response_artifacts[0].code == "print('a')"


@pytest.mark.asyncio
async def test_update_response_autofills_missing_artifact_ids(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    store = ConversationStore(tmp_path)
    service = ConversationService(store, sender, now=Clock())
    session = await service.create_session(gemini_tab())
    await service.begin_turn(session.session_id, "Question")

    updated = await service.update_response(
        session.session_id,
        "Summary",
        "Summary",
        response_artifacts=[
            {
                "title": "Code snippet 1",
                "kind": "code-block",
                "language": "python",
                "code": "print('a')",
                "preview_type": "code",
            }
        ],
    )

    assert updated.turns[-1].response_artifacts[0].artifact_id == "artifact-1"


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
async def test_rebind_session_updates_tab_id_and_url(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    store = ConversationStore(tmp_path)
    service = ConversationService(store, sender, now=Clock())
    session = await service.create_session(gemini_tab())
    rebound = await service.rebind_session(
        session.session_id,
        TabInfo(
            id="TARGET-NEW",
            title="Gemini conversation",
            url="https://gemini.google.com/app/new",
            is_gemini=True,
            is_selected=True,
        ),
    )

    saved = store.load(session.session_id)

    assert rebound.tab_id == "TARGET-NEW"
    assert rebound.conversation_url == "https://gemini.google.com/app/new"
    assert saved.tab_id == "TARGET-NEW"
    assert saved.conversation_url == "https://gemini.google.com/app/new"


@pytest.mark.asyncio
async def test_rebind_session_rejects_active_turn(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    service = ConversationService(ConversationStore(tmp_path), sender, now=Clock())
    session = await service.create_session(gemini_tab())
    await service.begin_turn(session.session_id, "Question")

    with pytest.raises(SessionRebindError):
        await service.rebind_session(
            session.session_id,
            TabInfo(
                id="TARGET-NEW",
                title="Gemini conversation",
                url="https://gemini.google.com/app/new",
                is_gemini=True,
                is_selected=True,
            ),
        )


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


def test_legacy_artifact_fields_are_migrated_into_artifact_list(tmp_path: Path) -> None:
        session_path = tmp_path / "legacy.json"
        session_path.write_text(
                """{
    "session_id": "legacy",
    "title": "Gemini conversation",
    "conversation_url": "https://gemini.google.com/app/abc",
    "tab_id": "TARGET-123",
    "created_at": "2026-09-20T06:30:00Z",
    "updated_at": "2026-09-20T06:30:02Z",
    "turns": [
        {
            "turn_id": "turn-1",
            "question": "Question",
            "response_text": "Summary",
            "response_markdown": "Summary",
            "response_artifact_code": "<!DOCTYPE html><html></html>",
            "response_image_path": "/artifacts/legacy/turn-1.png",
            "sent_at": "2026-09-20T06:30:01Z",
            "completed_at": "2026-09-20T06:30:02Z",
            "status": "completed",
            "error": null
        }
    ]
}
""",
                encoding="utf-8",
        )

        loaded = ConversationStore(tmp_path).load("legacy")

        assert len(loaded.turns[-1].response_artifacts) == 1
        assert loaded.turns[-1].response_artifacts[0].preview_type == "html"


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