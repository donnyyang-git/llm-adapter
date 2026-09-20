import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, StreamingResponse

from llm_adapter.adapters.gemini import GeminiAdapter
from llm_adapter.chrome_manager import ChromeManager
from llm_adapter.config import settings
from llm_adapter.conversation_service import (
    ConversationService,
    QuestionSendError,
    SessionRebindError,
    TabBusyError,
)
from llm_adapter.event_broker import SessionEventBroker
from llm_adapter.models import (
    ChromeStatus,
    ConversationSession,
    SelectTabRequest,
    SendMessageRequest,
    TabInfo,
)
from llm_adapter.response_monitor import ResponseMonitor
from llm_adapter.storage import ConversationStore
from llm_adapter.turn_automation import GeminiTurnAutomation


STATIC_DIR = Path(__file__).parent / "static"


async def stream_session_events(
    broker: SessionEventBroker, session_id: str
) -> AsyncIterator[str]:
    async with broker.subscribe(session_id) as events:
        while True:
            try:
                event = await asyncio.wait_for(events.get(), timeout=15)
                yield f"event: {event.event}\ndata: {event.model_dump_json()}\n\n"
            except TimeoutError:
                yield ": keep-alive\n\n"


def create_app(
    chrome_manager: ChromeManager | None = None,
    conversation_service: ConversationService | None = None,
    turn_automation: GeminiTurnAutomation | None = None,
) -> FastAPI:
    manager = chrome_manager or ChromeManager(settings)
    adapter = GeminiAdapter()
    monitor = ResponseMonitor(
        first_response_timeout=settings.first_response_timeout_seconds,
        total_response_timeout=settings.total_response_timeout_seconds,
        stable_seconds=settings.response_stable_seconds,
        poll_interval=settings.response_poll_interval_seconds,
    )
    automation = turn_automation or GeminiTurnAutomation(manager, adapter, monitor)
    manages_automation = conversation_service is None
    service = conversation_service or ConversationService(
        ConversationStore(settings.conversations_dir), automation.send_question
    )
    background_tasks: set[asyncio.Task[ConversationSession]] = set()

    def forget_task(task: asyncio.Task[ConversationSession]) -> None:
        background_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await manager.close()

    app = FastAPI(title="LLM Adapter", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def chat_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/chrome/status", response_model=ChromeStatus)
    async def chrome_status() -> ChromeStatus:
        return manager.status

    @app.post("/api/chrome/start", response_model=ChromeStatus)
    async def start_chrome() -> ChromeStatus:
        return await manager.start()

    @app.get("/api/tabs", response_model=list[TabInfo])
    async def list_tabs() -> list[TabInfo]:
        return await manager.list_tabs()

    @app.post("/api/tabs/select", response_model=TabInfo)
    async def select_tab(request: SelectTabRequest) -> TabInfo:
        try:
            return await manager.select_tab(request.tab_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Tab was not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/sessions", response_model=ConversationSession, status_code=201)
    async def create_session() -> ConversationSession:
        try:
            tab = await manager.get_selected_tab()
            return await service.create_session(tab)
        except LookupError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/sessions", response_model=list[ConversationSession])
    async def list_sessions() -> list[ConversationSession]:
        return await service.list_sessions()

    @app.post(
        "/api/sessions/new-gemini",
        response_model=ConversationSession,
        status_code=201,
    )
    async def create_new_gemini_session() -> ConversationSession:
        try:
            tab = await manager.start_new_gemini_conversation()
            return await service.create_session(tab)
        except LookupError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/sessions/{session_id}", response_model=ConversationSession)
    async def get_session(session_id: str) -> ConversationSession:
        try:
            return await service.get_session(session_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Session was not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/sessions/{session_id}/events")
    async def session_events(session_id: str) -> StreamingResponse:
        try:
            await service.get_session(session_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Session was not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return StreamingResponse(
            stream_session_events(service.event_broker, session_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/sessions/{session_id}/messages",
        response_model=ConversationSession,
        status_code=202,
    )
    async def send_message(
        session_id: str, request: SendMessageRequest
    ) -> ConversationSession:
        try:
            session = await service.begin_turn(session_id, request.question)
            if manages_automation:
                task = asyncio.create_task(
                    automation.watch_turn(session_id, session.tab_id, service)
                )
                background_tasks.add(task)
                task.add_done_callback(forget_task)
            return session
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Session was not found.") from error
        except TabBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except QuestionSendError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/sessions/{session_id}/rebind",
        response_model=ConversationSession,
        status_code=200,
    )
    async def rebind_session(session_id: str) -> ConversationSession:
        # [修改] 2026-09-20 17:05 原因: 舊對話要明確接回新 Gemini tab，必須經由使用者選定 tab 後再重綁。 說明: API 只接受目前選取的 Gemini tab，避免自動切換造成誤接。
        try:
            tab = await manager.get_selected_tab()
            return await service.rebind_session(session_id, tab)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Session was not found.") from error
        except LookupError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except SessionRebindError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/sessions/{session_id}/recapture",
        response_model=ConversationSession,
        status_code=202,
    )
    async def recapture(session_id: str) -> ConversationSession:
        try:
            session = await service.resume_turn(session_id)
            task = asyncio.create_task(
                automation.watch_recaptured_turn(session_id, session.tab_id, service)
            )
            background_tasks.add(task)
            task.add_done_callback(forget_task)
            return session
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Session was not found.") from error
        except TabBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("llm_adapter.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
