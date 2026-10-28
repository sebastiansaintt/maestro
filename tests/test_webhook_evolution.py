import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.providers.whatsapp.base import OutboundTextMessage, WhatsAppProvider


class FakeWhatsAppProvider(WhatsAppProvider):
    def __init__(self) -> None:
        self.sent_messages: list[OutboundTextMessage] = []

    async def send_text(self, message: OutboundTextMessage) -> None:
        self.sent_messages.append(message)


@pytest.mark.asyncio
async def test_webhook_echoes_incoming_user_text_message() -> None:
    fake_whatsapp = FakeWhatsAppProvider()
    app = create_app(whatsapp_provider=fake_whatsapp, allow_default_llm=False)

    payload = {
        "event": "messages.upsert",
        "instance": "magic-tour",
        "data": {
            "key": {
                "remoteJid": "573001234567@s.whatsapp.net",
                "fromMe": False,
                "id": "BAE5A1234567890",
            },
            "pushName": "Juan Perez",
            "message": {
                "conversation": "Hola, quiero info de Ciudad Perdida",
            },
            "messageType": "conversation",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook/evolution",
            json=payload,
            headers={"x-request-id": "req-slice-1"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-slice-1"
    assert response.json() == {"status": "processed", "tenant_id": "magic-tour"}

    assert len(fake_whatsapp.sent_messages) == 1
    sent = fake_whatsapp.sent_messages[0]
    assert sent.instance == "magic-tour"
    assert sent.recipient_number == "573001234567"
    assert sent.text == "Eco [magic-tour]: Hola, quiero info de Ciudad Perdida"
    assert sent.request_id == "req-slice-1"


@pytest.mark.asyncio
async def test_webhook_ignores_messages_from_me() -> None:
    fake_whatsapp = FakeWhatsAppProvider()
    app = create_app(whatsapp_provider=fake_whatsapp)

    payload = {
        "event": "messages.upsert",
        "instance": "magic-tour",
        "data": {
            "key": {
                "remoteJid": "573001234567@s.whatsapp.net",
                "fromMe": True,
                "id": "BAE5A9999999999",
            },
            "message": {
                "conversation": "Eco [magic-tour]: Hola",
            },
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "from_me"}
    assert fake_whatsapp.sent_messages == []


@pytest.mark.asyncio
async def test_webhook_supports_extended_text_and_ignores_non_message_events() -> None:
    fake_whatsapp = FakeWhatsAppProvider()
    app = create_app(whatsapp_provider=fake_whatsapp, allow_default_llm=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ignored_resp = await client.post(
            "/webhook/evolution",
            json={"event": "connection.update", "instance": "magic-tour", "data": {}},
        )
        assert ignored_resp.status_code == 200
        assert ignored_resp.json() == {"status": "ignored", "reason": "unsupported_event"}
        assert fake_whatsapp.sent_messages == []

        ext_resp = await client.post(
            "/webhook/evolution",
            json={
                "event": "messages.upsert",
                "instance": "magic-tour",
                "data": {
                    "key": {
                        "remoteJid": "573119876543@s.whatsapp.net",
                        "fromMe": False,
                        "id": "BAE5A7777777",
                    },
                    "message": {
                        "extendedTextMessage": {
                            "text": "¿Tienen salidas a Minca mañana?",
                        }
                    },
                },
            },
        )
        assert ext_resp.status_code == 200
        assert len(fake_whatsapp.sent_messages) == 1
        assert fake_whatsapp.sent_messages[0].text == "Eco [magic-tour]: ¿Tienen salidas a Minca mañana?"


