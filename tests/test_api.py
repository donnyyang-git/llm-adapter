import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from llm_adapter.chrome_manager import ChromeManager
from llm_adapter.conversation_service import ConversationService
from llm_adapter.event_broker import SessionEvent, SessionEventBroker
from llm_adapter.main import create_app, stream_session_events
from llm_adapter.models import ChromeStatus, TabInfo
from llm_adapter.storage import ConversationStore
from llm_adapter.turn_automation import GeminiTurnAutomation


class FakeChromeManager:
    def __init__(self) -> None:
        self.status = ChromeStatus(state="stopped", connected=False)
        self.selected = False

    async def start(self) -> ChromeStatus:
        self.status = ChromeStatus(state="connected", connected=True)
        return self.status

    async def list_tabs(self) -> list[TabInfo]:
        return [
            TabInfo(
                id="tab-1",
                title="Gemini",
                url="https://gemini.google.com/app/abc",
                is_gemini=True,
                is_selected=self.selected,
            )
        ]

    async def select_tab(self, tab_id: str) -> TabInfo:
        if tab_id != "tab-1":
            raise KeyError(tab_id)
        self.selected = True
        return (await self.list_tabs())[0]

    async def get_selected_tab(self) -> TabInfo:
        if not self.selected:
            raise LookupError("No Gemini tab is selected.")
        return (await self.list_tabs())[0]

    async def start_new_gemini_conversation(self) -> TabInfo:
        # [修改] 2026-09-20 16:40 原因: 這組假物件要模擬新流程的復原入口。 說明: 允許在尚未選擇 tab 時直接建立新的 Gemini chat，避免 API 測試仍綁死舊行為。
        self.selected = True
        return (await self.list_tabs())[0]

    async def rebind_session(self, session_id: str, tab: TabInfo):
        return tab

    async def close(self) -> None:
        return None


def test_root_serves_chat_page() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "LLM Adapter" in response.text


def test_static_frontend_assets_are_served() -> None:
    client = TestClient(create_app())

    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "EventSource" in response.text


def test_chrome_status_starts_disconnected() -> None:
    client = TestClient(create_app())

    response = client.get("/api/chrome/status")

    assert response.status_code == 200
    assert response.json() == {
        "state": "stopped",
        "connected": False,
        "message": "Dedicated Chrome has not been started.",
    }


def test_start_and_list_tabs_use_chrome_manager() -> None:
    manager = cast(ChromeManager, FakeChromeManager())
    client = TestClient(create_app(manager))

    start_response = client.post("/api/chrome/start")
    tabs_response = client.get("/api/tabs")

    assert start_response.json()["connected"] is True
    assert tabs_response.json() == [
        {
            "id": "tab-1",
            "title": "Gemini",
            "url": "https://gemini.google.com/app/abc",
            "is_gemini": True,
            "is_selected": False,
        }
    ]

    select_response = client.post("/api/tabs/select", json={"tab_id": "tab-1"})
    assert select_response.status_code == 200
    assert select_response.json()["is_selected"] is True


def test_select_missing_tab_returns_not_found() -> None:
    manager = cast(ChromeManager, FakeChromeManager())
    client = TestClient(create_app(manager))

    response = client.post("/api/tabs/select", json={"tab_id": "missing"})

    assert response.status_code == 404


def test_session_api_persists_pending_before_single_send(tmp_path) -> None:
    sent: list[tuple[str, str]] = []

    async def sender(tab_id: str, question: str) -> None:
        sent.append((tab_id, question))

    manager = cast(ChromeManager, FakeChromeManager())
    service = ConversationService(ConversationStore(tmp_path), sender)
    client = TestClient(create_app(manager, service))
    client.post("/api/tabs/select", json={"tab_id": "tab-1"})

    create_response = client.post("/api/sessions")
    session_id = create_response.json()["session_id"]
    message_response = client.post(
        f"/api/sessions/{session_id}/messages", json={"question": "Question"}
    )
    load_response = client.get(f"/api/sessions/{session_id}")
    duplicate_response = client.post(
        f"/api/sessions/{session_id}/messages", json={"question": "Duplicate"}
    )

    assert create_response.status_code == 201
    assert message_response.status_code == 202
    assert message_response.json()["turns"][-1]["status"] == "generating"
    assert message_response.json()["turns"][-1]["response_artifacts"] == []
    assert load_response.json() == message_response.json()
    assert duplicate_response.status_code == 409
    assert sent == [("tab-1", "Question")]


def test_create_session_requires_selected_tab(tmp_path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    manager = cast(ChromeManager, FakeChromeManager())
    service = ConversationService(ConversationStore(tmp_path), sender)
    client = TestClient(create_app(manager, service))

    response = client.post("/api/sessions")

    assert response.status_code == 409


def test_session_history_and_new_gemini_session(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    manager = cast(ChromeManager, FakeChromeManager())
    service = ConversationService(ConversationStore(tmp_path), sender)
    client = TestClient(create_app(manager, service))
    client.post("/api/tabs/select", json={"tab_id": "tab-1"})

    created = client.post("/api/sessions/new-gemini")
    history = client.get("/api/sessions")

    assert created.status_code == 201
    assert [session["session_id"] for session in history.json()] == [
        created.json()["session_id"]
    ]


def test_new_gemini_session_opens_when_no_tab_was_selected(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    manager = cast(ChromeManager, FakeChromeManager())
    service = ConversationService(ConversationStore(tmp_path), sender)
    client = TestClient(create_app(manager, service))

    created = client.post("/api/sessions/new-gemini")

    assert created.status_code == 201
    assert created.json()["tab_id"] == "tab-1"


def test_rebind_session_uses_selected_tab(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    manager = cast(ChromeManager, FakeChromeManager())
    service = ConversationService(ConversationStore(tmp_path), sender)
    client = TestClient(create_app(manager, service))
    client.post("/api/tabs/select", json={"tab_id": "tab-1"})
    created = client.post("/api/sessions")

    manager.selected = False
    client.post("/api/tabs/select", json={"tab_id": "tab-1"})
    response = client.post(f"/api/sessions/{created.json()['session_id']}/rebind")

    assert response.status_code == 200
    assert response.json()["tab_id"] == "tab-1"


@pytest.mark.asyncio
async def test_sse_stream_formats_session_event() -> None:
    broker = SessionEventBroker()
    stream = stream_session_events(broker, "session-1")
    next_frame = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    await broker.publish(
        SessionEvent(
            event="response_update",
            session_id="session-1",
            turn_id="turn-1",
            data={"response_text": "Hello"},
        )
    )

    frame = await next_frame
    await stream.aclose()

    assert frame.startswith("event: response_update\ndata: ")
    assert '"session_id":"session-1"' in frame
    assert '"turn_id":"turn-1"' in frame


def test_events_for_missing_session_returns_not_found(tmp_path: Path) -> None:
    async def sender(_: str, __: str) -> None:
        return None

    service = ConversationService(ConversationStore(tmp_path), sender)
    client = TestClient(create_app(conversation_service=service))

    response = client.get("/api/sessions/missing/events")

    assert response.status_code == 404


def test_recapture_claims_active_turn_without_resending(tmp_path: Path) -> None:
    sends = 0

    async def sender(_: str, __: str) -> None:
        nonlocal sends
        sends += 1

    store = ConversationStore(tmp_path)
    original_service = ConversationService(store, sender)
    session = asyncio.run(original_service.create_session((asyncio.run(FakeChromeManager().list_tabs()))[0]))
    asyncio.run(original_service.begin_turn(session.session_id, "Question"))
    restarted_service = ConversationService(store, sender)
    automation = AsyncMock(spec=GeminiTurnAutomation)
    automation.watch_recaptured_turn.return_value = store.load(session.session_id)
    client = TestClient(
        create_app(
            cast(ChromeManager, FakeChromeManager()),
            restarted_service,
            cast(GeminiTurnAutomation, automation),
        )
    )

    response = client.post(f"/api/sessions/{session.session_id}/recapture")

    assert response.status_code == 202
    assert response.json()["turns"][-1]["status"] == "generating"
    assert sends == 1
    automation.watch_recaptured_turn.assert_awaited_once()