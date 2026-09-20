import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import uuid4

from llm_adapter.event_broker import SessionEvent, SessionEventBroker
from llm_adapter.models import ConversationSession, ConversationTurn, TabInfo, utc_now
from llm_adapter.storage import ConversationStore


QuestionSender = Callable[[str, str], Awaitable[None]]


class TabBusyError(RuntimeError):
    pass


class QuestionSendError(RuntimeError):
    pass


class ConversationService:
    def __init__(
        self,
        store: ConversationStore,
        sender: QuestionSender,
        now: Callable[[], datetime] = utc_now,
        event_broker: SessionEventBroker | None = None,
    ) -> None:
        self.store = store
        self.sender = sender
        self.now = now
        self.event_broker = event_broker or SessionEventBroker()
        self._tab_locks: dict[str, asyncio.Lock] = {}
        self._lock_registry_guard = asyncio.Lock()
        self._active_turns: dict[str, tuple[str, str]] = {}

    async def create_session(self, tab: TabInfo) -> ConversationSession:
        if not tab.is_gemini:
            raise ValueError("A session requires a Gemini tab.")
        timestamp = self.now()
        session = ConversationSession(
            session_id=f"{timestamp:%Y%m%d-%H%M%S}-{uuid4().hex[:4]}",
            title=tab.title or "Gemini conversation",
            conversation_url=tab.url,
            tab_id=tab.id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        await self._save(session)
        return session

    async def get_session(self, session_id: str) -> ConversationSession:
        return await asyncio.to_thread(self.store.load, session_id)

    async def list_sessions(self) -> list[ConversationSession]:
        return await asyncio.to_thread(self.store.list_sessions)

    async def begin_turn(self, session_id: str, question: str) -> ConversationSession:
        session = await self.get_session(session_id)
        lock = await self._acquire_tab(session.tab_id)
        keep_lock = False
        try:
            if session.turns and session.turns[-1].status in {"pending", "generating"}:
                raise TabBusyError("This tab already has an active turn.")

            timestamp = self.now()
            turn = ConversationTurn(
                turn_id=f"turn-{len(session.turns) + 1}",
                question=question,
                sent_at=timestamp,
            )
            session.turns.append(turn)
            session.updated_at = timestamp
            await self._save(session)
            await self._publish("queued", session, turn)

            try:
                await self.sender(session.tab_id, question)
                await self._publish("sent", session, turn)
            except Exception as error:
                failed_turn = ConversationTurn.model_validate(
                    {
                        **turn.model_dump(),
                        "status": "failed",
                        "completed_at": self.now(),
                        "error": str(error),
                    }
                )
                session.turns[-1] = failed_turn
                session.updated_at = failed_turn.completed_at or self.now()
                await self._save(session)
                await self._publish("failed", session, failed_turn)
                raise QuestionSendError(
                    "Question submission failed; it was not sent again."
                ) from error

            generating_turn = ConversationTurn.model_validate(
                {**turn.model_dump(), "status": "generating"}
            )
            session.turns[-1] = generating_turn
            session.updated_at = self.now()
            self._active_turns[session.tab_id] = (session_id, turn.turn_id)
            keep_lock = True
            await self._save(session)
            await self._publish("generating", session, generating_turn)
            return session
        finally:
            if not keep_lock:
                lock.release()

    async def complete_turn(
        self,
        session_id: str,
        response_text: str,
        response_markdown: str,
    ) -> ConversationSession:
        return await self._finish_turn(
            session_id,
            response_text=response_text,
            response_markdown=response_markdown,
            status="completed",
            error=None,
        )

    async def update_response(
        self,
        session_id: str,
        response_text: str,
        response_markdown: str,
    ) -> ConversationSession:
        session, turn = await self._get_active_turn(session_id)
        session.turns[-1] = ConversationTurn.model_validate(
            {
                **turn.model_dump(),
                "response_text": response_text,
                "response_markdown": response_markdown,
            }
        )
        session.updated_at = self.now()
        await self._save(session)
        await self._publish("response_update", session, session.turns[-1])
        return session

    async def resume_turn(self, session_id: str) -> ConversationSession:
        session = await self.get_session(session_id)
        lock = await self._acquire_tab(session.tab_id)
        keep_lock = False
        try:
            if not session.turns or session.turns[-1].status not in {
                "pending",
                "generating",
            }:
                raise ValueError("The session has no active turn to recapture.")
            self._active_turns[session.tab_id] = (
                session_id,
                session.turns[-1].turn_id,
            )
            keep_lock = True
            return session
        finally:
            if not keep_lock:
                lock.release()

    async def partial_turn(
        self,
        session_id: str,
        response_text: str,
        response_markdown: str,
        error: str,
    ) -> ConversationSession:
        return await self._finish_turn(
            session_id,
            response_text=response_text,
            response_markdown=response_markdown,
            status="partial",
            error=error,
        )

    async def fail_turn(self, session_id: str, error: str) -> ConversationSession:
        _, turn = await self._get_active_turn(session_id)
        return await self._finish_turn(
            session_id,
            response_text=turn.response_text,
            response_markdown=turn.response_markdown,
            status="failed",
            error=error,
        )

    async def _finish_turn(
        self,
        session_id: str,
        *,
        response_text: str,
        response_markdown: str,
        status: str,
        error: str | None,
    ) -> ConversationSession:
        session, turn = await self._get_active_turn(session_id)
        completed_at = self.now()
        session.turns[-1] = ConversationTurn.model_validate(
            {
                **turn.model_dump(),
                "response_text": response_text,
                "response_markdown": response_markdown,
                "completed_at": completed_at,
                "status": status,
                "error": error,
            }
        )
        session.updated_at = completed_at
        await self._save(session)
        await self._publish(status, session, session.turns[-1])
        self._release_tab(session.tab_id)
        return session

    async def _acquire_tab(self, tab_id: str) -> asyncio.Lock:
        async with self._lock_registry_guard:
            lock = self._tab_locks.setdefault(tab_id, asyncio.Lock())
            if lock.locked():
                raise TabBusyError("This tab already has an active turn.")
            await lock.acquire()
            return lock

    async def _get_active_turn(
        self, session_id: str
    ) -> tuple[ConversationSession, ConversationTurn]:
        session = await self.get_session(session_id)
        if not session.turns or session.turns[-1].status not in {"pending", "generating"}:
            raise ValueError("The session has no active turn.")
        active = self._active_turns.get(session.tab_id)
        if active is not None and active != (session_id, session.turns[-1].turn_id):
            raise TabBusyError("This tab is active in another session.")
        return session, session.turns[-1]

    def _release_tab(self, tab_id: str) -> None:
        self._active_turns.pop(tab_id, None)
        lock = self._tab_locks.get(tab_id)
        if lock is not None and lock.locked():
            lock.release()

    async def _save(self, session: ConversationSession) -> None:
        await asyncio.to_thread(self.store.save, session)

    async def _publish(
        self,
        event: str,
        session: ConversationSession,
        turn: ConversationTurn,
    ) -> None:
        await self.event_broker.publish(
            SessionEvent(
                event=event,
                session_id=session.session_id,
                turn_id=turn.turn_id,
                data={
                    "status": turn.status,
                    "response_text": turn.response_text,
                    "response_markdown": turn.response_markdown,
                    "error": turn.error,
                },
            )
        )