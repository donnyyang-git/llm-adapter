import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import uuid4

from llm_adapter.event_broker import SessionEvent, SessionEventBroker
from llm_adapter.models import ConversationSession, ConversationTurn, ResponseArtifact, TabInfo, utc_now
from llm_adapter.storage import ConversationStore


QuestionSender = Callable[[str, str], Awaitable[None]]
ArtifactPayload = list[ResponseArtifact | dict[str, object]] | None


class TabBusyError(RuntimeError):
    pass


class SessionRebindError(RuntimeError):
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
        response_image_bytes: bytes | None = None,
        response_artifact_code: str = "",
        response_artifacts: ArtifactPayload = None,
    ) -> ConversationSession:
        return await self._finish_turn(
            session_id,
            response_text=response_text,
            response_markdown=response_markdown,
            response_artifact_code=response_artifact_code,
            response_image_bytes=response_image_bytes,
            response_artifacts=response_artifacts,
            status="completed",
            error=None,
        )

    async def update_response(
        self,
        session_id: str,
        response_text: str,
        response_markdown: str,
        response_image_bytes: bytes | None = None,
        response_artifact_code: str = "",
        response_artifacts: ArtifactPayload = None,
    ) -> ConversationSession:
        session, turn = await self._get_active_turn(session_id)
        image_path = await self._save_response_image(
            session.session_id, turn.turn_id, response_image_bytes
        )
        artifacts, legacy_code, legacy_image_path = self._merge_response_artifacts(
            response_artifacts=response_artifacts,
            response_artifact_code=response_artifact_code,
            response_image_path=image_path or turn.response_image_path,
            existing_artifacts=turn.response_artifacts,
        )
        session.turns[-1] = ConversationTurn.model_validate(
            {
                **turn.model_dump(),
                "response_text": response_text,
                "response_markdown": response_markdown,
                "response_artifacts": [artifact.model_dump() for artifact in artifacts],
                "response_artifact_code": legacy_code,
                "response_image_path": legacy_image_path,
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

    async def rebind_session(self, session_id: str, tab: TabInfo) -> ConversationSession:
        session = await self.get_session(session_id)
        if not tab.is_gemini:
            raise ValueError("A session requires a Gemini tab.")
        if session.turns and session.turns[-1].status in {"pending", "generating"}:
            raise SessionRebindError("The session has an active turn and cannot be rebound.")

        # [修改] 2026-09-20 17:05 原因: 舊對話需要能明確接回新的 Gemini tab，但不能默默改寫或影響正在生成中的 turn。 說明: 僅在沒有 active turn 時更新 tab_id / conversation_url，保留 session 內容與歷史。
        session.tab_id = tab.id
        session.conversation_url = tab.url
        session.updated_at = self.now()
        await self._save(session)
        return session

    async def partial_turn(
        self,
        session_id: str,
        response_text: str,
        response_markdown: str,
        error: str,
        response_image_bytes: bytes | None = None,
        response_artifact_code: str = "",
        response_artifacts: ArtifactPayload = None,
    ) -> ConversationSession:
        return await self._finish_turn(
            session_id,
            response_text=response_text,
            response_markdown=response_markdown,
            response_artifact_code=response_artifact_code,
            response_image_bytes=response_image_bytes,
            response_artifacts=response_artifacts,
            status="partial",
            error=error,
        )

    async def fail_turn(self, session_id: str, error: str) -> ConversationSession:
        _, turn = await self._get_active_turn(session_id)
        # [修改] 2026-09-20 18:05 原因: turn 失敗收尾時仍需保留先前已擷取的回覆附圖。 說明: 失敗路徑不重新產生圖片，改沿用既有 response_image_path。
        return await self._finish_turn(
            session_id,
            response_text=turn.response_text,
            response_markdown=turn.response_markdown,
            response_artifact_code=turn.response_artifact_code,
            response_image_bytes=None,
            response_artifacts=turn.response_artifacts,
            status="failed",
            error=error,
        )

    async def _finish_turn(
        self,
        session_id: str,
        *,
        response_text: str,
        response_markdown: str,
        response_artifact_code: str,
        response_image_bytes: bytes | None,
        response_artifacts: ArtifactPayload,
        status: str,
        error: str | None,
    ) -> ConversationSession:
        session, turn = await self._get_active_turn(session_id)
        completed_at = self.now()
        image_path = await self._save_response_image(
            session.session_id, turn.turn_id, response_image_bytes
        )
        artifacts, legacy_code, legacy_image_path = self._merge_response_artifacts(
            response_artifacts=response_artifacts,
            response_artifact_code=response_artifact_code,
            response_image_path=image_path or turn.response_image_path,
            existing_artifacts=turn.response_artifacts,
        )
        session.turns[-1] = ConversationTurn.model_validate(
            {
                **turn.model_dump(),
                "response_text": response_text,
                "response_markdown": response_markdown,
                "response_artifacts": [artifact.model_dump() for artifact in artifacts],
                "response_artifact_code": legacy_code,
                "response_image_path": legacy_image_path,
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

    def _merge_response_artifacts(
        self,
        *,
        response_artifacts: ArtifactPayload,
        response_artifact_code: str,
        response_image_path: str,
        existing_artifacts: list[ResponseArtifact],
    ) -> tuple[list[ResponseArtifact], str, str]:
        # [修改] 2026-09-20 19:20 原因: 新流程要支援多 artifacts，但舊 session 仍可能只有單一 code/image 欄位。 說明: 將新舊資料合併成統一 artifact 陣列，並同步維持 legacy 主 artifact 欄位。
        artifacts: list[ResponseArtifact] = []
        if response_artifacts:
            artifacts = self._coerce_response_artifacts(response_artifacts)
        elif existing_artifacts:
            artifacts = [artifact.model_copy(deep=True) for artifact in existing_artifacts]
        elif response_artifact_code or response_image_path:
            preview_type = "none"
            normalized_code = response_artifact_code.lstrip().lower()
            if normalized_code.startswith("<!doctype html>") or normalized_code.startswith("<html"):
                preview_type = "html"
            elif response_artifact_code:
                preview_type = "code"
            elif response_image_path:
                preview_type = "image"
            artifacts = [
                ResponseArtifact(
                    artifact_id="artifact-1",
                    title="Generated artifact",
                    kind="gemini-ui" if response_artifact_code else "code-block",
                    language="html" if preview_type == "html" else "",
                    code=response_artifact_code,
                    preview_type=preview_type,
                    image_path=response_image_path,
                )
            ]

        if artifacts and response_artifact_code and not artifacts[0].code:
            artifacts[0] = artifacts[0].model_copy(update={"code": response_artifact_code})
        if artifacts and response_image_path and not artifacts[0].image_path:
            artifacts[0] = artifacts[0].model_copy(update={"image_path": response_image_path})

        legacy_code = artifacts[0].code if artifacts else response_artifact_code
        legacy_image_path = (
            artifacts[0].image_path if artifacts and artifacts[0].image_path else response_image_path
        )
        return artifacts, legacy_code, legacy_image_path

    def _coerce_response_artifacts(
        self, response_artifacts: ArtifactPayload
    ) -> list[ResponseArtifact]:
        if not response_artifacts:
            return []

        coerced: list[ResponseArtifact] = []
        for index, artifact in enumerate(response_artifacts, start=1):
            if isinstance(artifact, ResponseArtifact):
                coerced.append(
                    artifact if artifact.artifact_id else artifact.model_copy(update={"artifact_id": f"artifact-{index}"})
                )
                continue

            artifact_payload = dict(artifact)
            artifact_payload.setdefault("artifact_id", f"artifact-{index}")
            coerced.append(ResponseArtifact.model_validate(artifact_payload))

        return coerced

    async def _save_response_image(
        self,
        session_id: str,
        turn_id: str,
        response_image_bytes: bytes | None,
    ) -> str:
        # [修改] 2026-09-20 18:05 原因: 回覆圖片要和文字更新一起持久化，避免前端看到不存在的 URL。 說明: 只有拿到新的截圖 bytes 時才寫檔，否則維持既有圖片路徑。
        if response_image_bytes is None:
            return ""
        return await asyncio.to_thread(
            self.store.save_turn_image, session_id, turn_id, response_image_bytes
        )

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
                    "response_artifacts": [artifact.model_dump() for artifact in turn.response_artifacts],
                    "response_artifact_code": turn.response_artifact_code,
                    "response_image_path": turn.response_image_path,
                    "error": turn.error,
                },
            )
        )