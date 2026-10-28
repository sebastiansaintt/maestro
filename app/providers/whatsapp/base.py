from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class OutboundTextMessage:
    instance: str
    recipient_number: str
    text: str
    request_id: str


@dataclass(frozen=True)
class MediaPayload:
    base64_data: str
    mimetype: str


class WhatsAppProviderError(Exception):
    """Error lanzado cuando falla la comunicación con el proveedor de WhatsApp."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WhatsAppProvider(ABC):
    @abstractmethod
    async def send_text(self, message: OutboundTextMessage) -> str | None:
        """Envía un mensaje de texto saliente. Devuelve el ID del mensaje si el proveedor lo informa."""
        raise NotImplementedError

    async def get_media_base64(self, instance: str, message_id: str) -> MediaPayload:
        """Descarga el contenido multimedia (audio/imagen/documento) de un mensaje entrante."""
        raise NotImplementedError

    async def delete_message(
        self, instance: str, remote_jid: str, message_id: str, from_me: bool = True
    ) -> bool:
        """Elimina un mensaje para todos en el chat si el proveedor lo soporta."""
        return False


class SentMessageRegistry:
    """Recuerda los IDs de los mensajes enviados por el bot para distinguirlos de los del asesor (fromMe)."""

    def __init__(self, max_size: int = 5000) -> None:
        self._order: deque[str] = deque()
        self._ids: set[str] = set()
        self._max_size = max_size

    def add(self, message_id: str) -> None:
        if not message_id or message_id in self._ids:
            return
        self._order.append(message_id)
        self._ids.add(message_id)
        while len(self._order) > self._max_size:
            self._ids.discard(self._order.popleft())

    def __contains__(self, message_id: object) -> bool:
        return message_id in self._ids


class TrackingWhatsAppProvider(WhatsAppProvider):
    """Decorador que registra el ID de todo mensaje enviado (respuestas, alertas, cierre suave)."""

    def __init__(self, inner: WhatsAppProvider, registry: SentMessageRegistry) -> None:
        self._inner = inner
        self.registry = registry

    async def send_text(self, message: OutboundTextMessage) -> str | None:
        message_id = await self._inner.send_text(message)
        if isinstance(message_id, str):
            self.registry.add(message_id)
        return message_id

    async def get_media_base64(self, instance: str, message_id: str) -> MediaPayload:
        return await self._inner.get_media_base64(instance, message_id)

    async def delete_message(
        self, instance: str, remote_jid: str, message_id: str, from_me: bool = True
    ) -> bool:
        return await self._inner.delete_message(instance, remote_jid, message_id, from_me)
