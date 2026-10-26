import time
import httpx

from app.core.logging import external_call_duration_ms, get_logger
from app.providers.whatsapp.base import (
    MediaPayload,
    OutboundTextMessage,
    WhatsAppProvider,
    WhatsAppProviderError,
)


class EvolutionWhatsAppProvider(WhatsAppProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def send_text(self, message: OutboundTextMessage) -> str | None:
        log = get_logger()
        url = f"{self._base_url}/message/sendText/{message.instance}"
        headers = {
            "apikey": self._api_key,
            "x-request-id": message.request_id,
        }
        payload = {
            "number": message.recipient_number,
            "text": message.text,
        }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            external_call_duration_ms.observe(
                ("evolution_api", "send_text", "network_error"),
                duration_ms,
            )
            log.error(
                "whatsapp_send_failed",
                provider="evolution_api",
                instance=message.instance,
                errorType=type(exc).__name__,
                latencyMs=duration_ms,
            )
            raise WhatsAppProviderError(
                f"Network error calling Evolution API for instance {message.instance}"
            ) from exc

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        status_class = f"{response.status_code // 100}xx"
        external_call_duration_ms.observe(
            ("evolution_api", "send_text", status_class),
            duration_ms,
        )

        if response.status_code >= 400:
            log.error(
                "whatsapp_send_failed",
                provider="evolution_api",
                instance=message.instance,
                statusCode=response.status_code,
                latencyMs=duration_ms,
            )
            raise WhatsAppProviderError(
                f"Evolution API returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        log.info(
            "whatsapp_send_succeeded",
            provider="evolution_api",
            instance=message.instance,
            statusCode=response.status_code,
            latencyMs=duration_ms,
        )
        try:
            body = response.json()
        except ValueError:
            return None
        key = body.get("key") if isinstance(body, dict) else None
        message_id = key.get("id") if isinstance(key, dict) else None
        return str(message_id) if message_id else None

    async def get_media_base64(self, instance: str, message_id: str) -> MediaPayload:
        log = get_logger()
        url = f"{self._base_url}/chat/getBase64FromMediaMessage/{instance}"
        payload = {"message": {"key": {"id": message_id}}, "convertToMp4": False}

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds * 3,
                transport=self._transport,
            ) as client:
                response = await client.post(url, headers={"apikey": self._api_key}, json=payload)
        except httpx.HTTPError as exc:
            external_call_duration_ms.observe(
                ("evolution_api", "get_media", "network_error"),
                round((time.perf_counter() - start) * 1000, 2),
            )
            log.error("whatsapp_media_download_failed", instance=instance, errorType=type(exc).__name__)
            raise WhatsAppProviderError(f"Network error downloading media for instance {instance}") from exc

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        external_call_duration_ms.observe(
            ("evolution_api", "get_media", f"{response.status_code // 100}xx"),
            duration_ms,
        )
        if response.status_code >= 400:
            log.error(
                "whatsapp_media_download_failed",
                instance=instance,
                statusCode=response.status_code,
                latencyMs=duration_ms,
            )
            raise WhatsAppProviderError(
                f"Evolution API returned HTTP {response.status_code} downloading media",
                status_code=response.status_code,
            )

        body = response.json()
        mimetype = str(body.get("mimetype") or "application/octet-stream").split(";")[0].strip()
        return MediaPayload(base64_data=str(body.get("base64") or ""), mimetype=mimetype)
