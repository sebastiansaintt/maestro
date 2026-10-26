from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from app.conversation.lifecycle import (
    SOFT_CLOSE_MESSAGE,
    SessionLifecycleManager,
)
from app.conversation.orchestrator import ConversationOrchestrator
from app.db.repository import SessionRepository
from app.llm.base import LLMRequest, LLMResponse
from app.providers.whatsapp.base import OutboundTextMessage


class FakeWhatsApp:
    def __init__(self) -> None:
        self.sent: list[OutboundTextMessage] = []

    async def send_text(self, message: OutboundTextMessage) -> None:
        self.sent.append(message)


class FakeLLM:
    async def generate_reply(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text="Respuesta simulada",
            tokens_in=1000,
            tokens_out=50,
            cost_usd=0.00018,
            model="gemini-3.8-flash",
            duration_ms=250.0,
        )


def _setup_tenant(tmp_path: Path) -> Path:
    t_dir = tmp_path / "magic-tour"
    (t_dir / "knowledge" / "tours").mkdir(parents=True)
    (t_dir / "config.yaml").write_text(
        'tenant_id: "magic-tour"\nname: "Magic Tour Colombia"\n', encoding="utf-8"
    )
    (t_dir / "knowledge" / "tours" / "tour.md").write_text(
        "# Tour\n100.000 COP", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_session_lifecycle_manager_sends_soft_close_after_10_minutes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "lifecycle.db"
    repo = SessionRepository(db_path=db_path)
    await repo.init_db()

    wa = FakeWhatsApp()
    manager = SessionLifecycleManager(
        repository=repo,
        whatsapp_provider=wa,
        idle_seconds=600.0,
    )

    t0 = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    session, _ = await repo.get_or_create_active_session(
        tenant_id="magic-tour",
        phone_number="573004445566",
        now=t0,
    )
    await repo.append_message(
        session_id=session.id,
        role="user",
        msg_type="text",
        content="Información por favor",
        timestamp=t0,
    )

    # At 5 minutes: no soft close yet
    t_5m = t0 + timedelta(minutes=5)
    sent_count_5m = await manager.process_idle_sessions(now=t_5m)
    assert sent_count_5m == 0
    assert len(wa.sent) == 0

    # At 10 minutes + 1 second: soft close triggered
    t_10m = t0 + timedelta(minutes=10, seconds=1)
    sent_count_10m = await manager.process_idle_sessions(now=t_10m)
    assert sent_count_10m == 1
    assert len(wa.sent) == 1
    assert wa.sent[0].recipient_number == "573004445566"
    assert wa.sent[0].text == SOFT_CLOSE_MESSAGE

    # SQLite session status updated to CIERRE_SUAVE
    messages = await repo.list_session_messages(session.id)
    assert len(messages) == 2
    assert messages[1].content == SOFT_CLOSE_MESSAGE

    # Repeating tick does NOT send duplicate soft close
    sent_count_again = await manager.process_idle_sessions(now=t_10m + timedelta(minutes=2))
    assert sent_count_again == 0
    assert len(wa.sent) == 1


@pytest.mark.asyncio
async def test_orchestrator_with_sqlite_persists_messages_and_token_events(
    tmp_path: Path,
) -> None:
    tenants_root = _setup_tenant(tmp_path)
    db_path = tmp_path / "persisted.db"
    repo = SessionRepository(db_path=db_path)
    await repo.init_db()

    wa = FakeWhatsApp()
    llm = FakeLLM()

    orchestrator = ConversationOrchestrator(
        whatsapp_provider=wa,
        llm_provider=llm,
        tenants_root=tenants_root,
        repository=repo,
    )

    reply = await orchestrator.handle_text_message(
        tenant_id="magic-tour",
        instance="magic-tour",
        sender_number="573007778899",
        text="Hola tour",
        request_id="req-persist-1",
    )
    assert reply == "Respuesta simulada"
    assert len(wa.sent) == 1

    # Verify session and messages were written to SQLite
    session, is_new = await repo.get_or_create_active_session(
        tenant_id="magic-tour",
        phone_number="573007778899",
    )
    assert is_new is False
    messages = await repo.list_session_messages(session.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Hola tour"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Respuesta simulada"
