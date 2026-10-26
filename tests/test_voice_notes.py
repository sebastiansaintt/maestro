import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db.repository import SessionRepository
from app.llm.base import LLMRequest, LLMResponse, TranscriptionRequest
from app.llm.gemini import GeminiLLMProvider
from app.main import create_app
from app.providers.whatsapp.base import MediaPayload, OutboundTextMessage

TOURIST_JID = "573004448899@s.whatsapp.net"


class FakeWhatsApp:
    def __init__(self) -> None:
        self.sent: list[OutboundTextMessage] = []
        self.media_requests: list[str] = []

    async def send_text(self, message: OutboundTextMessage) -> str | None:
        self.sent.append(message)
        return f"BOT-{len(self.sent)}"

    async def get_media_base64(self, instance: str, message_id: str) -> MediaPayload:
        self.media_requests.append(message_id)
        return MediaPayload(base64_data="T2dnUw==", mimetype="audio/ogg")


class FakeVoiceLLM:
    def __init__(self, transcript: str, reply: dict) -> None:
        self._transcript = transcript
        self._reply = reply
        self.transcriptions: list[TranscriptionRequest] = []
        self.requests: list[LLMRequest] = []

    async def transcribe_audio(self, request: TranscriptionRequest) -> LLMResponse:
        self.transcriptions.append(request)
        return LLMResponse(
            text=self._transcript, tokens_in=300, tokens_out=40, cost_usd=0.00007,
            model="gemini-3.8-flash", duration_ms=400.0,
        )

    async def generate_reply(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            text=json.dumps(self._reply, ensure_ascii=False), tokens_in=1500, tokens_out=50,
            cost_usd=0.00025, model="gemini-3.8-flash", duration_ms=300.0,
        )


def _tenant(tmp_path: Path) -> Path:
    t_dir = tmp_path / "magic-tour"
    (t_dir / "knowledge" / "tours").mkdir(parents=True)
    (t_dir / "config.yaml").write_text('tenant_id: "magic-tour"\nname: "Magic Tour Colombia"\n', encoding="utf-8")
    (t_dir / "knowledge" / "tours" / "minca.md").write_text("# Minca\n- Precio: 180.000 COP\n", encoding="utf-8")
    return tmp_path


def _audio_payload(seconds: int, msg_id: str) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "magic-tour",
        "data": {
            "key": {"remoteJid": TOURIST_JID, "fromMe": False, "id": msg_id},
            "message": {"audioMessage": {"seconds": seconds, "mimetype": "audio/ogg; codecs=opus", "ptt": True}},
            "messageType": "audioMessage",
        },
    }


@pytest.mark.asyncio
async def test_voice_note_over_90_seconds_is_rejected_without_download_or_gemini(tmp_path: Path) -> None:
    repo = SessionRepository(db_path=tmp_path / "v1.db")
    await repo.init_db()
    wa = FakeWhatsApp()
    llm = FakeVoiceLLM(transcript="", reply={})
    client = TestClient(create_app(whatsapp_provider=wa, llm_provider=llm, tenants_root=_tenant(tmp_path), repository=repo))

    res = client.post("/webhook/evolution", json=_audio_payload(seconds=120, msg_id="A-LONG"))

    assert res.json()["status"] == "audio_rejected"
    assert wa.media_requests == []          # no se descarga
    assert llm.transcriptions == [] and llm.requests == []  # no se gasta Gemini
    assert len(wa.sent) == 1
    assert "90" in wa.sent[0].text


@pytest.mark.asyncio
async def test_short_voice_note_is_transcribed_stored_and_answered_through_guarded_flow(tmp_path: Path) -> None:
    repo = SessionRepository(db_path=tmp_path / "v2.db")
    await repo.init_db()
    wa = FakeWhatsApp()
    llm = FakeVoiceLLM(
        transcript="Hola, ¿cuánto cuesta el tour a Minca?",
        reply={"reply": "El tour a Minca cuesta 180.000 COP.", "intent": "none", "language": "es", "lead": {}},
    )
    client = TestClient(create_app(whatsapp_provider=wa, llm_provider=llm, tenants_root=_tenant(tmp_path), repository=repo))

    res = client.post("/webhook/evolution", json=_audio_payload(seconds=15, msg_id="A-SHORT"))
    assert res.json()["status"] == "processed"

    # Descarga + transcripción con el audio real
    assert wa.media_requests == ["A-SHORT"]
    assert llm.transcriptions[0].base64_data == "T2dnUw=="
    assert llm.transcriptions[0].mimetype == "audio/ogg"

    # La transcripción entra al mismo flujo blindado (delimitador XML)
    assert llm.requests[0].user_message == (
        "<MENSAJE_USUARIO>\nHola, ¿cuánto cuesta el tour a Minca?\n</MENSAJE_USUARIO>"
    )
    assert wa.sent[-1].text == "El tour a Minca cuesta 180.000 COP."

    # RF-19: la transcripción queda en el historial
    session, _ = await repo.get_or_create_active_session(tenant_id="magic-tour", phone_number="573004448899")
    messages = await repo.list_session_messages(session.id)
    assert messages[0].msg_type == "audio"
    assert messages[0].audio_transcription == "Hola, ¿cuánto cuesta el tour a Minca?"

    usage = await repo.list_events("magic-tour", event_type="TOKEN_USAGE")
    assert {e.payload.get("kind") for e in usage} == {"transcription", "conversation"}


@pytest.mark.asyncio
async def test_gemini_transcribe_audio_sends_inline_audio_and_returns_text() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "Hola, quiero ir a Tayrona"}]}}],
                "usageMetadata": {"promptTokenCount": 320, "candidatesTokenCount": 12},
            },
        )

    provider = GeminiLLMProvider(api_key="k", model="gemini-3.8-flash", transport=httpx.MockTransport(handler))
    result = await provider.transcribe_audio(
        TranscriptionRequest(tenant_id="magic-tour", base64_data="T2dnUw==", mimetype="audio/ogg")
    )

    assert result.text == "Hola, quiero ir a Tayrona"
    assert result.tokens_in == 320
    body = json.loads(captured[0].content.decode("utf-8"))
    parts = body["contents"][0]["parts"]
    assert parts[0]["inlineData"] == {"mimeType": "audio/ogg", "data": "T2dnUw=="}
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
