from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.conversation.handoff import HandoffService
from app.conversation.office_hours import OfficeHoursPolicy
from app.db.repository import SessionRepository
from app.providers.whatsapp.base import OutboundTextMessage

BOGOTA = ZoneInfo("America/Bogota")
GROUP_JID = "120363000000000000@g.us"


class FakeWhatsApp:
    def __init__(self) -> None:
        self.sent: list[OutboundTextMessage] = []

    async def send_text(self, message: OutboundTextMessage) -> str | None:
        self.sent.append(message)
        return f"BOT-{len(self.sent)}"


async def _session_with_lead(repo: SessionRepository, now: datetime):
    session, _ = await repo.get_or_create_active_session(
        tenant_id="magic-tour", phone_number="573001112233", now=now
    )
    await repo.update_session(
        session.id,
        reservation_data={
            "name": "Laura",
            "tour_slug": "ciudad-perdida-santa-marta-4-dias",
            "date": "15 de noviembre",
            "people": 2,
        },
    )
    return await repo.get_session(session.id)


@pytest.mark.asyncio
async def test_handoff_in_office_hours_alerts_group_pauses_24h_and_records_event(
    tmp_path: Path,
) -> None:
    repo = SessionRepository(db_path=tmp_path / "h.db")
    await repo.init_db()
    wa = FakeWhatsApp()
    service = HandoffService(repository=repo, whatsapp_provider=wa, office_hours=OfficeHoursPolicy())

    now = datetime(2026, 10, 21, 10, 0, tzinfo=BOGOTA)
    session = await _session_with_lead(repo, now)

    result = await service.trigger(
        tenant_id="magic-tour",
        instance="magic-tour",
        advisor_group_jid=GROUP_JID,
        session=session,
        phone_number="573001112233",
        reason="human_advisor",
        language="es",
        request_id="req-h1",
        now=now,
    )

    # Alerta al grupo de asesores con el resumen del lead (RF-12)
    assert len(wa.sent) == 1
    alert = wa.sent[0]
    assert alert.recipient_number == GROUP_JID
    for expected in (
        "Laura",
        "+573001112233",
        "ciudad-perdida-santa-marta-4-dias",
        "15 de noviembre",
        "2",
        "Solicita asesor",
        "https://wa.me/573001112233",
    ):
        assert expected in alert.text
    assert "PRIORIDAD ALTA" not in alert.text

    # Pausa de 24 h (RF-13)
    updated = await repo.get_session(session.id)
    assert updated.status == "ESPERANDO_ASESOR"
    assert updated.paused_until == now + timedelta(hours=24)

    # Aviso al turista dentro de horario: sin mención a las 8:00 a. m.
    assert result.is_off_hours is False
    assert "8:00" not in result.tourist_notice
    assert "asesor" in result.tourist_notice

    events = await repo.list_events("magic-tour")
    assert [e.event_type for e in events] == ["HANDOFF"]
    assert events[0].payload["reason"] == "human_advisor"
    assert events[0].is_off_hours is False


@pytest.mark.asyncio
async def test_handoff_off_hours_with_receipt_is_high_priority_and_warns_8am(
    tmp_path: Path,
) -> None:
    repo = SessionRepository(db_path=tmp_path / "h2.db")
    await repo.init_db()
    wa = FakeWhatsApp()
    service = HandoffService(repository=repo, whatsapp_provider=wa, office_hours=OfficeHoursPolicy())

    night = datetime(2026, 10, 21, 22, 15, tzinfo=BOGOTA)
    session = await _session_with_lead(repo, night)

    result = await service.trigger(
        tenant_id="magic-tour",
        instance="magic-tour",
        advisor_group_jid=GROUP_JID,
        session=session,
        phone_number="573001112233",
        reason="payment_receipt",
        language="en",
        request_id="req-h2",
        now=night,
    )

    assert "PRIORIDAD ALTA" in wa.sent[0].text
    assert result.is_off_hours is True
    assert "8:00 a.m." in result.tourist_notice  # RF-14b, en inglés
    events = await repo.list_events("magic-tour")
    assert events[0].is_off_hours is True
