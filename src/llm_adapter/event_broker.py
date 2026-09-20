import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import BaseModel, Field


class SessionEvent(BaseModel):
    event: str
    session_id: str
    turn_id: str | None = None
    data: dict[str, object] = Field(default_factory=dict)


class SessionEventBroker:
    def __init__(self, queue_size: int = 100) -> None:
        self.queue_size = queue_size
        self._subscribers: dict[str, set[asyncio.Queue[SessionEvent]]] = defaultdict(set)
        self._guard = asyncio.Lock()

    async def publish(self, event: SessionEvent) -> None:
        async with self._guard:
            subscribers = tuple(self._subscribers.get(event.session_id, ()))
        for queue in subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(
        self, session_id: str
    ) -> AsyncIterator[asyncio.Queue[SessionEvent]]:
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue(self.queue_size)
        async with self._guard:
            self._subscribers[session_id].add(queue)
        try:
            yield queue
        finally:
            async with self._guard:
                subscribers = self._subscribers.get(session_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(session_id, None)