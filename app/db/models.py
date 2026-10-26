from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ContactRecord:
    id: int
    tenant_id: str
    phone_number: str
    name: str | None
    language: str
    created_at: datetime


@dataclass(frozen=True)
class SessionRecord:
    id: int
    tenant_id: str
    contact_id: int
    status: str  # "ACTIVA" | "ESPERANDO_ASESOR" | "ESPERANDO_PAGO" | "CIERRE_SUAVE" | "ARCHIVADA"
    reservation_data: dict = field(default_factory=dict)
    paused_until: datetime | None = None
    last_activity: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class MessageRecord:
    id: int
    session_id: int
    role: str  # "user" | "assistant" | "advisor"
    msg_type: str  # "text" | "audio" | "image"
    content: str
    audio_transcription: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class EventRecord:
    id: int
    tenant_id: str
    session_id: int | None
    event_type: str
    payload: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    is_off_hours: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)
