from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from llm_adapter.adapters.gemini import GeminiAdapter, GeminiPageStatus, GeminiResponse
from llm_adapter.chrome_manager import ChromeManager
from llm_adapter.conversation_service import ConversationService
from llm_adapter.models import TabInfo
from llm_adapter.response_monitor import ResponseMonitor, ResponseTimeoutError
from llm_adapter.storage import ConversationStore
from llm_adapter.turn_automation import GeminiTurnAutomation


def tab() -> TabInfo:
    return TabInfo(
        id="TARGET-123",
        title="Gemini",
        url="https://gemini.google.com/app/abc",
        is_gemini=True,
        is_selected=True,
    )


@pytest.mark.asyncio
async def test_completed_watch_saves_response_and_releases_lock(tmp_path: Path) -> None:
    page = object()
    manager = AsyncMock()
    manager.get_page.return_value = page
    adapter = AsyncMock(spec=GeminiAdapter)
    adapter.inspect.return_value = GeminiPageStatus(is_gemini=True, ready=True)
    adapter.count_model_messages.return_value = 2
    response = GeminiResponse(
        text="Answer", markdown="**Answer**", generating=False, input_editable=True
    )
    monitor = AsyncMock(spec=ResponseMonitor)
    monitor.wait.return_value = response
    automation = GeminiTurnAutomation(cast(ChromeManager, manager), adapter, monitor)
    service = ConversationService(ConversationStore(tmp_path), automation.send_question)
    session = await service.create_session(tab())

    await service.begin_turn(session.session_id, "Question")
    completed = await automation.watch_turn(session.session_id, tab().id, service)
    next_turn = await service.begin_turn(session.session_id, "Next")

    assert adapter.send_question.await_args_list[0].args == (page, "Question")
    assert adapter.send_question.await_args_list[1].args == (page, "Next")
    assert adapter.send_question.await_count == 2
    assert completed.turns[-1].status == "completed"
    assert completed.turns[-1].response_markdown == "**Answer**"
    assert next_turn.turns[-1].turn_id == "turn-2"


@pytest.mark.asyncio
async def test_total_timeout_saves_partial_and_releases_lock(tmp_path: Path) -> None:
    partial = GeminiResponse(
        text="Partial", markdown="Partial", generating=True, input_editable=False
    )
    manager = AsyncMock()
    manager.get_page.return_value = object()
    adapter = AsyncMock(spec=GeminiAdapter)
    adapter.inspect.return_value = GeminiPageStatus(is_gemini=True, ready=True)
    adapter.count_model_messages.return_value = 0
    monitor = AsyncMock(spec=ResponseMonitor)
    monitor.wait.side_effect = ResponseTimeoutError("total", partial)
    automation = GeminiTurnAutomation(cast(ChromeManager, manager), adapter, monitor)
    service = ConversationService(ConversationStore(tmp_path), automation.send_question)
    session = await service.create_session(tab())

    await service.begin_turn(session.session_id, "Question")
    result = await automation.watch_turn(session.session_id, tab().id, service)

    assert result.turns[-1].status == "partial"
    assert result.turns[-1].response_text == "Partial"


@pytest.mark.asyncio
async def test_recapture_monitors_last_response_without_resending(tmp_path: Path) -> None:
    page = object()
    manager = AsyncMock()
    manager.get_page.return_value = page
    adapter = AsyncMock(spec=GeminiAdapter)
    adapter.count_model_messages.return_value = 3
    response = GeminiResponse(
        text="Recovered",
        markdown="Recovered",
        generating=False,
        input_editable=True,
    )
    monitor = AsyncMock(spec=ResponseMonitor)
    monitor.wait.return_value = response
    automation = GeminiTurnAutomation(cast(ChromeManager, manager), adapter, monitor)

    async def original_sender(_: str, __: str) -> None:
        return None

    store = ConversationStore(tmp_path)
    first_service = ConversationService(store, original_sender)
    session = await first_service.create_session(tab())
    await first_service.begin_turn(session.session_id, "Question")
    restarted_service = ConversationService(store, automation.send_question)
    await restarted_service.resume_turn(session.session_id)

    result = await automation.watch_recaptured_turn(
        session.session_id, tab().id, restarted_service
    )
    sample = monitor.wait.await_args.args[0]
    await sample()

    adapter.capture_latest_response.assert_awaited_once_with(page, 2)
    adapter.send_question.assert_not_awaited()
    assert result.turns[-1].status == "completed"
    assert result.turns[-1].response_text == "Recovered"