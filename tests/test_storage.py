from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from llm_adapter.models import ConversationSession, ConversationTurn
from llm_adapter.storage import ConversationStore


def make_session() -> ConversationSession:
    timestamp = datetime(2026, 9, 20, 6, 30, tzinfo=timezone.utc)
    completed = datetime(2026, 9, 20, 6, 31, tzinfo=timezone.utc)
    return ConversationSession(
        session_id="20260920-143000-abcd",
        title="Gemini conversation",
        conversation_url="https://gemini.google.com/app/abc",
        tab_id="TARGET-123",
        created_at=timestamp,
        updated_at=timestamp,
        turns=[
            ConversationTurn(
                turn_id="turn-1",
                question="第一個問題",
                sent_at=timestamp,
                completed_at=completed,
                status="completed",
                response_text="回答內容",
                response_markdown="回答內容",
            )
        ],
    )


def test_saves_and_loads_json_and_markdown(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    session = make_session()

    json_path, markdown_path = store.save(session)
    loaded = store.load(session.session_id)

    assert loaded == session
    assert json_path.name == f"{session.session_id}.json"
    rendered = markdown_path.read_text(encoding="utf-8")
    assert "### User\n\n第一個問題" in rendered
    assert "Sent: 2026-09-20T06:30:00+00:00" in rendered
    assert "Completed: 2026-09-20T06:31:00+00:00" in rendered
    assert not list(tmp_path.glob("*.tmp"))


def test_second_save_atomically_replaces_both_files(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    session = make_session()
    store.save(session)
    updated = session.model_copy(update={"title": "Updated title"})

    store.save(updated)

    assert store.load(session.session_id).title == "Updated title"
    assert (tmp_path / f"{session.session_id}.md").read_text(encoding="utf-8").startswith(
        "# Updated title"
    )
    assert not list(tmp_path.glob("*.tmp"))


def test_lists_sessions_with_newest_first(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    older = make_session()
    newer = older.model_copy(
        update={
            "session_id": "20260920-143100-efgh",
            "updated_at": datetime(2026, 9, 20, 6, 31, tzinfo=timezone.utc),
        }
    )
    store.save(older)
    store.save(newer)

    sessions = store.list_sessions()

    assert [session.session_id for session in sessions] == [
        newer.session_id,
        older.session_id,
    ]


def test_rejects_path_traversal_session_id(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)

    with pytest.raises(ValueError, match="Invalid session ID"):
        store.load("../outside")


def test_finished_turn_requires_completion_time() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        ConversationTurn(turn_id="turn-1", question="Question", status="completed")