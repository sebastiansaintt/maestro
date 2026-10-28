from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.conversation.office_hours import OfficeHoursPolicy
from app.core.logging import get_logger
from app.db.models import SessionRecord
from app.db.repository import SessionRepository
from app.providers.whatsapp.base import (
    OutboundTextMessage,
    WhatsAppProvider,
    WhatsAppProviderError,
)

logger = get_logger()

_REASON_LABELS_ES: dict[str, str] = {
    "human_advisor": "Solicita asesor humano",
    "payment_receipt": "Comprobante de pago recibido",
    "pqr": "PQR / Reclamo o caso especial",
    "availability": "Confirmación de cupo / disponibilidad",
    "out_of_knowledge": "Consulta fuera del catálogo",
}


@dataclass(frozen=True)
class HandoffResult:
    paused_until: datetime
    is_off_hours: bool
    tourist_notice: str
    advisor_alert_text: str


class HandoffService:
    """Gestiona el pase a asesor humano: alerta al grupo, pausa de 24 h y aviso de horario (RF-11..RF-14b)."""

    def __init__(
        self,
        repository: SessionRepository,
        whatsapp_provider: WhatsAppProvider,
        office_hours: OfficeHoursPolicy | None = None,
        pause_hours: int = 24,
    ) -> None:
        self._repository = repository
        self._whatsapp_provider = whatsapp_provider
        self._office_hours = office_hours or OfficeHoursPolicy()
        self._pause_hours = pause_hours

    async def trigger(
        self,
        *,
        tenant_id: str,
        instance: str,
        advisor_group_jid: str,
        session: SessionRecord,
        phone_number: str,
        reason: str,
        language: str = "es",
        request_id: str = "",
        now: datetime | None = None,
    ) -> HandoffResult:
        now_dt = now or datetime.now(timezone.utc)
        paused_until = now_dt + timedelta(hours=self._pause_hours)
        is_off_hours = self._office_hours.is_off_hours(now_dt)

        lead = session.reservation_data or {}
        name = str(lead.get("name") or "Sin nombre aún")
        tour = str(lead.get("tour_slug") or lead.get("tour") or "Por definir")
        travel_date = str(lead.get("date") or "Por definir")
        people = str(lead.get("people") or "Por definir")
        origin = str(lead.get("origin") or "No especificado")

        clean_digits = phone_number.lstrip("+").split("@")[0]
        reason_label = _REASON_LABELS_ES.get(reason, reason)
        high_priority = reason in {"payment_receipt", "pqr"}
        header = (
            "🚨 *ALERTA PRIORIDAD ALTA — MAESTRO*"
            if high_priority
            else "🔔 *NUEVO PASE A ASESOR — MAESTRO*"
        )

        advisor_alert_text = (
            f"{header}\n"
            f"• *Motivo:* {reason_label}\n"
            f"• *Cliente:* {name}\n"
            f"• *Teléfono:* +{clean_digits}\n"
            f"• *Tour:* {tour}\n"
            f"• *Fecha tentativa:* {travel_date}\n"
            f"• *Personas:* {people}\n"
            f"• *Origen:* {origin}\n"
            f"• *Chat directo:* https://wa.me/{clean_digits}"
        )

        await self._repository.update_session(
            session.id,
            status="ESPERANDO_ASESOR",
            paused_until=paused_until,
        )

        await self._repository.record_event(
            tenant_id=tenant_id,
            session_id=session.id,
            event_type="HANDOFF",
            payload={
                "reason": reason,
                "high_priority": high_priority,
                "phone_number": clean_digits,
                "lead": lead,
            },
            is_off_hours=is_off_hours,
            timestamp=now_dt,
        )

        if advisor_group_jid:
            try:
                await self._whatsapp_provider.send_text(
                    OutboundTextMessage(
                        instance=instance,
                        recipient_number=advisor_group_jid,
                        text=advisor_alert_text,
                        request_id=request_id,
                    )
                )
            except (WhatsAppProviderError, Exception) as exc:
                logger.warning(
                    "advisor_group_alert_failed",
                    tenantId=tenant_id,
                    advisorGroupJid=advisor_group_jid,
                    error=str(exc),
                )

        tourist_notice = self._build_tourist_notice(
            reason=reason,
            language=language,
            is_off_hours=is_off_hours,
        )

        logger.info(
            "handoff_triggered",
            tenantId=tenant_id,
            sessionId=session.id,
            reason=reason,
            highPriority=high_priority,
            isOffHours=is_off_hours,
        )

        return HandoffResult(
            paused_until=paused_until,
            is_off_hours=is_off_hours,
            tourist_notice=tourist_notice,
            advisor_alert_text=advisor_alert_text,
        )

    def _build_tourist_notice(
        self,
        *,
        reason: str,
        language: str,
        is_off_hours: bool,
    ) -> str:
        lang = "en" if language == "en" else "es"
        if lang == "en":
            base = (
                "Thank you! I have shared your payment receipt with our team so an advisor can verify it and confirm your booking."
                if reason == "payment_receipt"
                else "I have notified our human advisor team so they can assist you personally."
            )
            if is_off_hours:
                return f"{base} Our team will reply starting at 8:00 a.m. (Colombia time)."
            return base

        base_es = (
            "¡Gracias! He enviado tu comprobante a nuestro equipo para que un asesor valide el ingreso y te confirme la reserva."
            if reason == "payment_receipt"
            else "Ya notifiqué a un asesor humano de nuestro equipo para atenderte personalmente."
        )
        if is_off_hours:
            return f"{base_es} Nuestro equipo te responderá a partir de las 8:00 a. m."
        return base_es
