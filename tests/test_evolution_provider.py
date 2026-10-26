import json
import httpx
import pytest

from app.providers.whatsapp.base import OutboundTextMessage, WhatsAppProviderError
from app.providers.whatsapp.evolution import EvolutionWhatsAppProvider


@pytest.mark.asyncio
async def test_evolution_provider_sends_text_with_expected_headers_and_body() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(201, json={"key": {"id": "MSG-001"}, "status": "PENDING"})

    transport = httpx.MockTransport(handler)
    provider = EvolutionWhatsAppProvider(
        base_url="http://evolution.local:8080",
        api_key="test-secret-api-key",
        transport=transport,
    )

    msg = OutboundTextMessage(
        instance="magic-tour",
        recipient_number="573001234567",
        text="Eco [magic-tour]: Hola",
        request_id="req-seam-2",
    )
    await provider.send_text(msg)

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "POST"
    assert str(req.url) == "http://evolution.local:8080/message/sendText/magic-tour"
    assert req.headers["apikey"] == "test-secret-api-key"
    assert req.headers["x-request-id"] == "req-seam-2"
    assert json.loads(req.content.decode("utf-8")) == {
        "number": "573001234567",
        "text": "Eco [magic-tour]: Hola",
    }


@pytest.mark.asyncio
async def test_evolution_provider_raises_domain_error_on_http_failure_without_leaking_api_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "Bad Gateway"})

    provider = EvolutionWhatsAppProvider(
        base_url="http://evolution.local:8080",
        api_key="SUPER_SECRET_KEY_DO_NOT_LOG",
        transport=httpx.MockTransport(failing_handler),
    )

    msg = OutboundTextMessage(
        instance="magic-tour",
        recipient_number="573001234567",
        text="Eco [magic-tour]: Hola",
        request_id="req-fail-502",
    )

    with pytest.raises(WhatsAppProviderError) as exc_info:
        await provider.send_text(msg)

    assert exc_info.value.status_code == 502
    captured_out = capsys.readouterr().out
    assert "whatsapp_send_failed" in captured_out
    assert "SUPER_SECRET_KEY_DO_NOT_LOG" not in captured_out



@pytest.mark.asyncio
async def test_evolution_provider_returns_sent_message_id_and_downloads_media_base64() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.startswith("/message/sendText"):
            return httpx.Response(201, json={"key": {"id": "MSG-XYZ"}, "status": "PENDING"})
        return httpx.Response(201, json={"base64": "T2dnUw==", "mimetype": "audio/ogg; codecs=opus"})

    provider = EvolutionWhatsAppProvider(
        base_url="http://evolution.local:8080",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )

    msg_id = await provider.send_text(
        OutboundTextMessage(instance="magic-tour", recipient_number="573001234567", text="Hola", request_id="r1")
    )
    assert msg_id == "MSG-XYZ"

    media = await provider.get_media_base64(instance="magic-tour", message_id="AUDIO-1")
    assert media.base64_data == "T2dnUw=="
    assert media.mimetype == "audio/ogg"
    req = captured[1]
    assert str(req.url) == "http://evolution.local:8080/chat/getBase64FromMediaMessage/magic-tour"
    assert json.loads(req.content.decode("utf-8")) == {"message": {"key": {"id": "AUDIO-1"}}, "convertToMp4": False}
