from llm_adapter.adapters.gemini import GeminiAdapter, GeminiAdapterError
from llm_adapter.chrome_manager import ChromeManager
from llm_adapter.conversation_service import ConversationService
from llm_adapter.models import ConversationSession
from llm_adapter.response_monitor import ResponseMonitor, ResponseTimeoutError


class GeminiTurnAutomation:
    def __init__(
        self,
        chrome_manager: ChromeManager,
        adapter: GeminiAdapter,
        monitor: ResponseMonitor,
    ) -> None:
        self.chrome_manager = chrome_manager
        self.adapter = adapter
        self.monitor = monitor
        self._baselines: dict[str, int] = {}

    async def send_question(self, tab_id: str, question: str) -> None:
        page = await self.chrome_manager.get_page(tab_id)
        readiness = await self.adapter.inspect(page)
        if not readiness.ready:
            raise GeminiAdapterError(readiness.message or "Gemini is not ready.")
        self._baselines[tab_id] = await self.adapter.count_model_messages(page)
        await self.adapter.send_question(page, question)

    async def watch_turn(
        self,
        session_id: str,
        tab_id: str,
        service: ConversationService,
    ) -> ConversationSession:
        baseline_count = self._baselines.pop(tab_id)
        return await self._watch_response(session_id, tab_id, baseline_count, service)

    async def watch_recaptured_turn(
        self,
        session_id: str,
        tab_id: str,
        service: ConversationService,
    ) -> ConversationSession:
        try:
            page = await self.chrome_manager.get_page(tab_id)
            response_count = await self.adapter.count_model_messages(page)
            baseline_count = max(response_count - 1, 0)
            return await self._watch_response(
                session_id, tab_id, baseline_count, service, page=page
            )
        except Exception as error:
            return await service.fail_turn(session_id, str(error))

    async def _watch_response(
        self,
        session_id: str,
        tab_id: str,
        baseline_count: int,
        service: ConversationService,
        *,
        page=None,
    ) -> ConversationSession:
        try:
            if page is None:
                page = await self.chrome_manager.get_page(tab_id)

            async def sample():
                return await self.adapter.capture_latest_response(page, baseline_count)

            async def update(response):
                await service.update_response(
                    session_id,
                    response.text,
                    response.markdown,
                    response_image_bytes=response.screenshot_png,
                    response_artifact_code=response.artifact_code,
                    response_artifacts=self._artifacts_payload(response.artifacts),
                )

            response = await self.monitor.wait(sample, update)
            return await service.complete_turn(
                session_id,
                response.text,
                response.markdown,
                response_image_bytes=response.screenshot_png,
                response_artifact_code=response.artifact_code,
                response_artifacts=self._artifacts_payload(response.artifacts),
            )
        except ResponseTimeoutError as error:
            if error.partial is not None:
                return await service.partial_turn(
                    session_id,
                    error.partial.text,
                    error.partial.markdown,
                    str(error),
                    response_image_bytes=error.partial.screenshot_png,
                    response_artifact_code=error.partial.artifact_code,
                    response_artifacts=self._artifacts_payload(error.partial.artifacts),
                )
            return await service.fail_turn(session_id, str(error))
        except Exception as error:
            return await service.fail_turn(session_id, str(error))

    @staticmethod
    def _artifacts_payload(artifacts) -> list[dict[str, object]]:
        payload = []
        for index, artifact in enumerate(artifacts, start=1):
            payload.append(
                {
                    "artifact_id": f"artifact-{index}",
                    **artifact.model_dump(),
                }
            )
        return payload