from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import uuid

from app.core.logging import get_logger
from app.db.repository import SessionRepository
from app.providers.whatsapp.base import OutboundTextMessage, WhatsAppProvider

logger = get_logger()

# Official soft close copy defined in PRD §4.6 (RF-16)
SOFT_CLOSE_MESSAGE = (
    "¡Perfecto! Tómate tu tiempo para pensarlo 🌴. Guardo tu cotización por 4 días; "
    "escríbeme cuando quieras y seguimos donde quedamos."
)


class SessionLifecycleManager:
    """Manages 10-minute idle soft-close (RF-16) and background session maintenance."""

    def __init__(
        self,
        repository: SessionRepository,
        whatsapp_provider: WhatsAppProvider,
        idle_seconds: float = 600.0,
    ) -> None:
        self._repository = repository
        self._whatsapp_provider = whatsapp_provider
        self._idle_seconds = idle_seconds

    async def process_idle_sessions(self, now: datetime | None = None) -> int:
        now_dt = now or datetime.now(timezone.utc)
        due_sessions = await self._repository.find_sessions_due_for_soft_close(
            now=now_dt,
            idle_seconds=self._idle_seconds,
        )

        sent_count = 0
        for session in due_sessions:
            contact = await self._repository.get_contact_by_id(session.contact_id)
            if contact is None:
                continue

            req_id = str(uuid.uuid4())
            try:
                await self._whatsapp_provider.send_text(
                    OutboundTextMessage(
                        instance=session.tenant_id,
                        recipient_number=contact.phone_number,
                        text=SOFT_CLOSE_MESSAGE,
                        request_id=req_id,
                    )
                )
                await self._repository.append_message(
                    session_id=session.id,
                    role="assistant",
                    msg_type="text",
                    content=SOFT_CLOSE_MESSAGE,
                    timestamp=now_dt,
                )
                await self._repository.mark_soft_closed(session.id)
                sent_count += 1

                logger.info(
                    "session_soft_closed",
                    sessionId=session.id,
                    tenantId=session.tenant_id,
                    recipientNumber=contact.phone_number,
                    requestId=req_id,
                )
            except Exception as exc:
                logger.error(
                    "session_soft_close_failed",
                    sessionId=session.id,
                    tenantId=session.tenant_id,
                    recipientNumber=contact.phone_number,
                    errorType=type(exc).__name__,
                )

        return sent_count

    async def run_periodic_checker(self, interval_seconds: float = 60.0) -> None:
        while True:
            try:
                await self.process_idle_sessions()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "lifecycle_checker_loop_error",
                    errorType=type(exc).__name__,
                )
            await asyncio.sleep(interval_seconds)
