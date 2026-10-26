import json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from app.db.repository import SessionRepository
from app.llm.base import LLMRequest, LLMResponse
from app.main import create_app
from app.providers.whatsapp.base import OutboundTextMessage

ADVISOR_GROUP = "120363000000000000@g.us"


class FakeWhatsAppProvider:
    def __init__(self) -> None:
        self.sent_messages: list[OutboundTextMessage] = []

    async def send_text(self, message: OutboundTextMessage) -> str | None:
        self.sent_messages.append(message)
        return f"BOT-MSG-{len(self.sent_messages)}"


class FakeStructuredLLM:
    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.requests: list[LLMRequest] = []

    async def generate_reply(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        payload = self._payloads.pop(0) if self._payloads else {"reply": "OK", "intent": "none"}
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            tokens_in=1400,
            tokens_out=60,
            cost_usd=0.000246,
            model="gemini-3.8-flash",
            duration_ms=210.0,
        )


def _setup_magic_tour_tenant(tmp_path: Path) -> Path:
    t_dir = tmp_path / "magic-tour"
    (t_dir / "knowledge" / "tours").mkdir(parents=True)
    (t_dir / "config.yaml").write_text(
        'tenant_id: "magic-tour"\n'
        'name: "Magic Tour Colombia"\n'
        f'advisor_group_jid: "{ADVISOR_GROUP}"\n'
        'privacy_policy_url: "https://magictourcolombia.com/es/politica-de-tratamiento-de-datos-personales"\n',
        encoding="utf-8",
    )
    (t_dir / "pagos.example.yaml").write_text(
        'is_demo: true\n'
        'holder_name: "MAGIC TOUR COLOMBIA S.A.S (DEMO)"\n'
        'nit: "900.000.000-0 (DEMO)"\n'
        'accounts:\n'
        '  - method: "Bancolombia Ahorros"\n'
        '    number: "000-000000-00 (DEMO)"\n'
        '  - method: "Nequi"\n'
        '    number: "300 000 0000 (DEMO)"\n'
        'instructions_es: "Envíanos foto o PDF del comprobante por este mismo chat."\n'
        'instructions_en: "Please send a photo or PDF of the receipt in this chat."\n',
        encoding="utf-8",
    )
    (t_dir / "knowledge" / "tours" / "ciudad-perdida-santa-marta-4-dias.md").write_text(
        "# Ciudad Perdida Santa Marta Tour 4 días\n"
        "- Destino: Ciudad Perdida Colombia | Tipo: tour\n"
        "- Precio: 1.860.000 COP (Reserva con: 10%)\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_booking_flow_blocks_premature_payment_then_unlocks_after_checklist(
    tmp_path: Path,
) -> None:
    tenants_root = _setup_magic_tour_tenant(tmp_path)
    repo = SessionRepository(db_path=tmp_path / "booking.db")
    await repo.init_db()

    wa = FakeWhatsAppProvider()
    llm = FakeStructuredLLM(
        [
            # Turno 1: usuario malintencionado pide cuenta de entrada sin haber cotizado
            {
                "reply": "Aquí tienes las cuentas",
                "intent": "pay_direct",
                "language": "es",
                "lead": {},
            },
            # Turno 2: usuario pregunta por Ciudad Perdida y da sus datos; el bot le cotiza el precio oficial
            {
                "reply": (
                    "¡Hola Laura! El tour a Ciudad Perdida de 4 días cuesta 1.860.000 COP por persona. "
                    "Para 2 personas el 15 de noviembre puedes pagar directo o hablar con un asesor."
                ),
                "intent": "none",
                "language": "es",
                "lead": {
                    "name": "Laura Gómez",
                    "tour_slug": "ciudad-perdida-santa-marta-4-dias",
                    "people": 2,
                    "date": "15 de noviembre",
                    "origin": "Medellín",
                },
            },
            # Turno 3: con el checklist completo y el precio ya cotizado, pide pagar directo
            {
                "reply": "¡Excelente elección Laura!",
                "intent": "pay_direct",
                "language": "es",
                "lead": {},
            },
        ]
    )

    app = create_app(
        whatsapp_provider=wa,
        llm_provider=llm,
        tenants_root=tenants_root,
        repository=repo,
    )
    client = TestClient(app)

    # Turno 1: Intento prematuro de pedir cuentas -> bloqueado por el checklist determinista
    client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "U-1"},
                "message": {"conversation": "Pásame el número de cuenta Bancolombia para transferir ya"},
            },
        },
    )
    assert len(wa.sent_messages) == 1
    first_reply = wa.sent_messages[0].text
    assert "000-000000-00" not in first_reply
    assert "300 000 0000" not in first_reply
    assert "tu nombre" in first_reply
    assert "el tour que te interesa" in first_reply

    # Turno 2: El usuario consulta el tour y entrega sus datos; el bot cotiza 1.860.000 COP
    client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "U-2"},
                "message": {
                    "conversation": "Soy Laura Gómez de Medellín, quiero Ciudad Perdida 4 días para 2 personas el 15 de noviembre"
                },
            },
        },
    )
    assert len(wa.sent_messages) == 2
    assert "1.860.000 COP" in wa.sent_messages[1].text

    # Turno 3: Ahora sí pide pagar directo -> el checklist está 100% completo y entrega las cuentas
    client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": "573001112233@s.whatsapp.net", "fromMe": False, "id": "U-3"},
                "message": {"conversation": "Listo, quiero pagar directo por transferencia"},
            },
        },
    )
    assert len(wa.sent_messages) == 3
    payment_reply = wa.sent_messages[2].text
    assert "Bancolombia Ahorros: 000-000000-00 (DEMO)" in payment_reply
    assert "Nequi: 300 000 0000 (DEMO)" in payment_reply

    events = await repo.list_events("magic-tour", event_type="PAYMENT_INFO")
    assert len(events) == 1


@pytest.mark.asyncio
async def test_handoff_pauses_bot_for_24h_and_advisor_commands_control_session(
    tmp_path: Path,
) -> None:
    tenants_root = _setup_magic_tour_tenant(tmp_path)
    repo = SessionRepository(db_path=tmp_path / "handoff_e2e.db")
    await repo.init_db()

    wa = FakeWhatsAppProvider()
    llm = FakeStructuredLLM(
        [
            {
                "reply": "¡Claro Carlos! Ya te comunico con uno de nuestros asesores.",
                "intent": "human_advisor",
                "language": "es",
                "lead": {
                    "name": "Carlos Ruiz",
                    "tour_slug": "ciudad-perdida-santa-marta-4-dias",
                    "people": 4,
                    "date": "20 de diciembre",
                },
            },
            {
                "reply": "¡Hola de nuevo Carlos! Ya estoy activo para ayudarte.",
                "intent": "none",
                "language": "es",
                "lead": {},
            },
        ]
    )

    app = create_app(
        whatsapp_provider=wa,
        llm_provider=llm,
        tenants_root=tenants_root,
        repository=repo,
    )
    client = TestClient(app)

    # 1. Turista pide hablar con un asesor -> se envía alerta al grupo y aviso al turista
    client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": "573009998877@s.whatsapp.net", "fromMe": False, "id": "M-1"},
                "message": {"conversation": "Hola soy Carlos Ruiz, somos 4 para el 20 de diciembre en Ciudad Perdida, quiero hablar con un asesor"},
            },
        },
    )
    # 2 mensajes enviados: alerta al grupo + respuesta al turista
    assert len(wa.sent_messages) == 2
    recipients = {m.recipient_number for m in wa.sent_messages}
    assert recipients == {ADVISOR_GROUP, "573009998877"}
    assert len(llm.requests) == 1

    # 2. Mientras está en pausa de 24h, el turista vuelve a escribir -> el bot NO llama a Gemini ni responde
    r_paused = client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": "573009998877@s.whatsapp.net", "fromMe": False, "id": "M-2"},
                "message": {"conversation": "¿Siguen ahí?"},
            },
        },
    )
    assert r_paused.json()["status"] == "paused_by_handoff"
    assert len(llm.requests) == 1
    assert len(wa.sent_messages) == 2

    # 3. El asesor envía #activar en el chat -> reactiva el bot
    r_activate = client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": "573009998877@s.whatsapp.net", "fromMe": True, "id": "ADV-CMD-1"},
                "message": {"conversation": "#activar"},
            },
        },
    )
    assert r_activate.json() == {"status": "advisor_command", "command": "activar"}

    # 4. El turista escribe otra vez -> ahora el bot sí responde
    client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": "573009998877@s.whatsapp.net", "fromMe": False, "id": "M-3"},
                "message": {"conversation": "Gracias, una pregunta más"},
            },
        },
    )
    assert len(llm.requests) == 2
    assert len(wa.sent_messages) == 3
    assert "Ya estoy activo" in wa.sent_messages[2].text


@pytest.mark.asyncio
async def test_bot_ignores_group_chatter_and_own_echoes_but_advisor_manual_message_resets_pause(
    tmp_path: Path,
) -> None:
    tenants_root = _setup_magic_tour_tenant(tmp_path)
    repo = SessionRepository(db_path=tmp_path / "advisor.db")
    await repo.init_db()
    wa = FakeWhatsAppProvider()
    llm = FakeStructuredLLM(
        [{"reply": "¡Hola! Soy el asistente.", "intent": "none", "language": "es", "lead": {}}]
    )
    client = TestClient(
        create_app(whatsapp_provider=wa, llm_provider=llm, tenants_root=tenants_root, repository=repo)
    )

    def post(jid: str, from_me: bool, msg_id: str, text: str) -> dict:
        return client.post(
            "/webhook/evolution",
            json={
                "event": "messages.upsert",
                "instance": "magic-tour",
                "data": {
                    "key": {"remoteJid": jid, "fromMe": from_me, "id": msg_id},
                    "message": {"conversation": text},
                },
            },
        ).json()

    # 1. Conversación en el grupo de asesores: el bot jamás responde allí
    assert post(ADVISOR_GROUP, False, "G-1", "Equipo, ¿quién toma el lead?")["reason"] == "group_message"
    assert llm.requests == [] and wa.sent_messages == []

    # 2. El turista escribe; el eco del propio bot (mismo ID devuelto por sendText) se ignora sin pausar
    post("573005551111@s.whatsapp.net", False, "T-1", "Hola")
    bot_msg_id = "BOT-MSG-1"
    assert post("573005551111@s.whatsapp.net", True, bot_msg_id, "¡Hola! Soy el asistente.")["reason"] == "bot_echo"
    session, _ = await repo.get_or_create_active_session(tenant_id="magic-tour", phone_number="573005551111")
    assert session.paused_until is None

    # 3. El asesor escribe manualmente al turista -> el bot se pausa 24 h (RF-13)
    assert post("573005551111@s.whatsapp.net", True, "ADV-9", "Hola, soy Andrea de Magic Tour")["status"] == "advisor_message"
    session = await repo.get_session(session.id)
    assert session.status == "ESPERANDO_ASESOR"
    assert session.paused_until is not None
    messages = await repo.list_session_messages(session.id)
    assert messages[-1].role == "advisor"


@pytest.mark.asyncio
async def test_out_of_knowledge_offers_advisor_without_pausing_and_records_unanswered_question(
    tmp_path: Path,
) -> None:
    tenants_root = _setup_magic_tour_tenant(tmp_path)
    repo = SessionRepository(db_path=tmp_path / "ook.db")
    await repo.init_db()
    wa = FakeWhatsAppProvider()
    llm = FakeStructuredLLM(
        [
            {
                "reply": "No tengo ese dato; si quieres te paso con un asesor.",
                "intent": "out_of_knowledge",
                "language": "es",
                "lead": {},
            }
        ]
    )
    client = TestClient(
        create_app(whatsapp_provider=wa, llm_provider=llm, tenants_root=tenants_root, repository=repo)
    )
    client.post(
        "/webhook/evolution",
        json={
            "event": "messages.upsert",
            "instance": "magic-tour",
            "data": {
                "key": {"remoteJid": "573002223344@s.whatsapp.net", "fromMe": False, "id": "Q-1"},
                "message": {"conversation": "¿Venden tiquetes aéreos a San Andrés?"},
            },
        },
    )

    assert [m.recipient_number for m in wa.sent_messages] == ["573002223344"]  # sin alerta al grupo
    session, _ = await repo.get_or_create_active_session(tenant_id="magic-tour", phone_number="573002223344")
    assert session.paused_until is None
    unanswered = await repo.list_events("magic-tour", event_type="UNANSWERED_QUESTION")
    assert len(unanswered) == 1
    assert "tiquetes" in unanswered[0].payload["question"]
