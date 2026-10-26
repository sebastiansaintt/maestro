from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LLMProviderError(Exception):
    """Domain error raised when the LLM provider fails or returns an invalid response."""


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "user" | "model"
    content: str


@dataclass(frozen=True)
class LLMRequest:
    tenant_id: str
    system_prompt: str
    user_message: str
    history: tuple[ChatMessage, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str
    duration_ms: float


@dataclass(frozen=True)
class TranscriptionRequest:
    tenant_id: str
    base64_data: str
    mimetype: str


class LLMProvider(Protocol):
    async def generate_reply(self, request: LLMRequest) -> LLMResponse:
        ...

    async def transcribe_audio(self, request: TranscriptionRequest) -> LLMResponse:
        ...
