from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger()

DebounceHandler = Callable[[str, str, str, str, str], Awaitable[None]]


@dataclass
class _SenderBuffer:
    tenant_id: str
    instance: str
    sender_number: str
    messages: list[str] = field(default_factory=list)
    latest_request_id: str = ""
    task: asyncio.Task[None] | None = None


class MessageDebouncer:
    """Consolidates rapid consecutive messages from the same sender into a single turn (RF-15)."""

    def __init__(
        self,
        handler: DebounceHandler,
        delay_seconds: float = 4.0,
    ) -> None:
        self._handler = handler
        self._delay_seconds = delay_seconds
        self._buffers: dict[tuple[str, str], _SenderBuffer] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        tenant_id: str,
        instance: str,
        sender_number: str,
        text: str,
        request_id: str,
    ) -> None:
        key = (tenant_id, sender_number)

        async with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                buf = _SenderBuffer(
                    tenant_id=tenant_id,
                    instance=instance,
                    sender_number=sender_number,
                )
                self._buffers[key] = buf

            buf.instance = instance
            buf.messages.append(text)
            buf.latest_request_id = request_id

            if buf.task is not None and not buf.task.done():
                buf.task.cancel()

            buf.task = asyncio.create_task(self._wait_and_dispatch(key))

    async def _wait_and_dispatch(self, key: tuple[str, str]) -> None:
        try:
            await asyncio.sleep(self._delay_seconds)
        except asyncio.CancelledError:
            return

        async with self._lock:
            buf = self._buffers.pop(key, None)

        if buf is None or not buf.messages:
            return

        combined_text = "\n".join(buf.messages).strip()
        count = len(buf.messages)

        logger.info(
            "messages_debounced",
            tenantId=buf.tenant_id,
            senderNumber=buf.sender_number,
            messageCount=count,
            totalChars=len(combined_text),
            delaySeconds=self._delay_seconds,
        )

        try:
            await self._handler(
                buf.tenant_id,
                buf.instance,
                buf.sender_number,
                combined_text,
                buf.latest_request_id,
            )
        except Exception as exc:
            logger.error(
                "debounced_handler_failed",
                tenantId=buf.tenant_id,
                senderNumber=buf.sender_number,
                errorType=type(exc).__name__,
            )

    async def shutdown(self) -> None:
        async with self._lock:
            for buf in self._buffers.values():
                if buf.task is not None and not buf.task.done():
                    buf.task.cancel()
            self._buffers.clear()
