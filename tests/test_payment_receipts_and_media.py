import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.repository import SessionRepository
from app.llm.base import LLMRequest, LLMResponse
from app.main import create_app
from app.providers.whatsapp.base import OutboundTextMessage

ADVISOR_GROUP = "120363000000000000@g.us"
TOURIST_JID = "573007771122@s.whatsapp.net"


class FakeWhatsApp:
    def __init__(self) -> None:
        self.sent: list[OutboundTextMessage] = []

    async def send_text(self, message: OutboundTextMessage) -> str | None:
        self.sent.append(message)
        return f"BOT-{len(self.sent)}"


class FakeLLM:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def generate_reply(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            text=json.dumps(
                {
                    "reply": "¡Gracias por la imagen! Esas botas con buen agarre son ideales para Ciudad Perdida.",
                    "intent": "none",
                    "language": "es",
                    "lead": {},
                },
                ensure_ascii=False,
            ),
            tokens_in=900,
            tokens_out=35,
            cost_usd=0.00015,
            model="gemini-3.8-flash",
            duration_ms=200.0,
        )


def _setup_tenant(tmp_path: Path) -> Path:
    t_dir = tmp_path / "magic-tour"
    (t_dir / "knowledge" / "tours").mkdir(parents=True)
    (t_dir / "config.yaml").write_text(
        'tenant_id: "magic-tour"\n'
        'name: "Magic Tour Colombia"\n'
        f'advisor_group_jid: "{ADVISOR_GROUP}"\n',
        encoding="utf-8",
    )
    (t_dir / "knowledge" / "tours" / "ciudad-perdida.md").write_text(
        "# Ciudad Perdida\n- Precio: 1.860.000 COP\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_payment_receipt_image_or_pdf_triggers_immediate_high_priority_handoff_and_never_confirms_booking(
    tmp_path: Path,
) -> None:
    repo = SessionRepository(db_path=tmp_path / "receipts.db")
    await repo.init_db()
    wa = FakeWhatsApp()
    llm = FakeLLM()

    # Pre-cargamos la sesión en ESPERANDO_PAGO con los datos del lead
    session, _ = await repo.get_or_create_active_session(
        tenant_id="magic-tour",
        phone_number="573007771122",
    )
    await repo.update_session(
        session.id,
        status="ESPERANDO_PAGO",
        reservation_data={
            "name": "Laura Gómez",
            "tour_slug": "ciudad-perdida-santa-marta-4-dias",
            "people": 2,
            "date": "15 de noviembre",
            "language": "es",
        },
    )

    client = TestClient(
        create_app(
            whatsapp_provider=wa,
            llm_provider=llm,
            tenants_root=_setup_tenant(tmp_path),
            repository=repo,
        )
    )

    res = client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": TOURIST_JID, "fromMe": False, "id": "IMG-REC-1"},
                "message": {
                    "imageMessage": {
                        "mimetype": "image/jpeg",
                        "caption": "Aquí está el comprobante de Nequi",
                    }
                },
                "messageType": "imageMessage",
            },
        },
    )

    assert res.status_code == 200
    assert res.json()["status"] == "receipt_handoff"
    # Cero confirmación financiera por IA: ni siquiera invoca al LLM para inventar confirmaciones
    assert llm.requests == []

    # Se envían 2 mensajes: 1 alerta de PRIORIDAD ALTA al grupo de asesores y 1 acuse al turista
    assert len(wa.sent) == 2
    group_msg = next(m for m in wa.sent if m.recipient_number == ADVISOR_GROUP)
    tourist_msg = next(m for m in wa.sent if m.recipient_number == "573007771122")

    assert "PRIORIDAD ALTA" in group_msg.text
    assert "Comprobante de pago recibido" in group_msg.text
    assert "Laura Gómez" in group_msg.text
    assert "https://wa.me/573007771122" in group_msg.text

    # El mensaje al turista agradece e informa que un asesor validará el comprobante (RF-10)
    assert "comprobante" in tourist_msg.text.lower()
    assert "asesor" in tourist_msg.text.lower()

    # La sesión queda pausada por 24 h en ESPERANDO_ASESOR
    updated = await repo.get_session(session.id)
    assert updated.status == "ESPERANDO_ASESOR"
    assert updated.paused_until is not None

    receipt_events = await repo.list_events("magic-tour", event_type="PAYMENT_RECEIPT")
    assert len(receipt_events) == 1


@pytest.mark.asyncio
async def test_non_receipt_image_in_regular_chat_is_acknowledged_without_false_receipt_alert(
    tmp_path: Path,
) -> None:
    repo = SessionRepository(db_path=tmp_path / "non_receipt.db")
    await repo.init_db()
    wa = FakeWhatsApp()
    llm = FakeLLM()

    client = TestClient(
        create_app(
            whatsapp_provider=wa,
            llm_provider=llm,
            tenants_root=_setup_tenant(tmp_path),
            repository=repo,
        )
    )

    res = client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": TOURIST_JID, "fromMe": False, "id": "IMG-BOOTS-1"},
                "message": {
                    "imageMessage": {
                        "mimetype": "image/jpeg",
                        "caption": "¿Estas botas sirven para el trekking?",
                    }
                },
                "messageType": "imageMessage",
            },
        },
    )

    assert res.status_code == 200
    assert res.json()["status"] == "processed"
    # No dispara alerta de comprobante al grupo
    assert [m.recipient_number for m in wa.sent] == ["573007771122"]
    assert "botas" in wa.sent[0].text
