from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from app.db.repository import SessionRepository


@pytest.mark.asyncio
async def test_session_repository_creates_session_retrieves_active_and_resumes_from_soft_close(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "test_maestro.db"
    repo = SessionRepository(db_path=db_file)
    await repo.init_db()

    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)

    # 1. New user arrives -> creates contact and new active session
    session1, is_new1 = await repo.get_or_create_active_session(
        tenant_id="magic-tour",
        phone_number="573001234567",
        now=t0,
    )
    assert is_new1 is True
    assert session1.tenant_id == "magic-tour"
    assert session1.status == "ACTIVA"
    assert session1.expires_at == t0 + timedelta(days=4)

    # Add message
    await repo.append_message(
        session_id=session1.id,
        role="user",
        msg_type="text",
        content="Hola, precio de Ciudad Perdida",
        timestamp=t0,
    )
    await repo.append_message(
        session_id=session1.id,
        role="assistant",
        msg_type="text",
        content="Cuesta 1.860.000 COP",
        timestamp=t0 + timedelta(seconds=2),
    )

    messages = await repo.list_session_messages(session_id=session1.id)
    assert len(messages) == 2
    assert messages[0].content == "Hola, precio de Ciudad Perdida"
    assert messages[1].content == "Cuesta 1.860.000 COP"

    # 2. Simulate 10 minutes idle -> eligible for soft close
    t_idle = t0 + timedelta(minutes=10, seconds=1)
    due_soft_close = await repo.find_sessions_due_for_soft_close(now=t_idle, idle_seconds=600)
    assert len(due_soft_close) == 1
    assert due_soft_close[0].id == session1.id

    await repo.mark_soft_closed(session_id=session1.id)

    # 3. User writes back after 1 day (< 4 days) -> resumes session, status back to ACTIVA, is_new=False (RF-17)
    t_return = t0 + timedelta(days=1)
    session2, is_new2 = await repo.get_or_create_active_session(
        tenant_id="magic-tour",
        phone_number="573001234567",
        now=t_return,
    )
    assert is_new2 is False
    assert session2.id == session1.id
    assert session2.status == "ACTIVA"
    # Expiration refreshed to 4 days from t_return
    assert session2.expires_at == t_return + timedelta(days=4)


@pytest.mark.asyncio
async def test_session_repository_expires_after_4_days_and_starts_clean_session(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "test_maestro_expiry.db"
    repo = SessionRepository(db_path=db_file)
    await repo.init_db()

    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)

    # User starts session
    session1, _ = await repo.get_or_create_active_session(
        tenant_id="magic-tour",
        phone_number="573119876543",
        now=t0,
    )

    await repo.append_message(
        session_id=session1.id,
        role="user",
        msg_type="text",
        content="Cotización inicial",
        timestamp=t0,
    )

    # User returns after 4 days + 1 hour (>= 4 days expiration, RF-18)
    t_expired = t0 + timedelta(days=4, hours=1)
    session2, is_new2 = await repo.get_or_create_active_session(
        tenant_id="magic-tour",
        phone_number="573119876543",
        now=t_expired,
    )

    assert is_new2 is True
    assert session2.id != session1.id
    assert session2.status == "ACTIVA"

    # Old session is archived and messages are isolated to new session
    old_messages = await repo.list_session_messages(session_id=session1.id)
    assert len(old_messages) == 1

    new_messages = await repo.list_session_messages(session_id=session2.id)
    assert len(new_messages) == 0


@pytest.mark.asyncio
async def test_session_repository_records_events_with_token_metrics(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "test_maestro_events.db"
    repo = SessionRepository(db_path=db_file)
    await repo.init_db()

    t0 = datetime(2026, 10, 20, 21, 30, 0, tzinfo=timezone.utc)  # Off hours
    session, _ = await repo.get_or_create_active_session(
        tenant_id="magic-tour",
        phone_number="573005556677",
        now=t0,
    )

    event = await repo.record_event(
        tenant_id="magic-tour",
        session_id=session.id,
        event_type="TOKEN_USAGE",
        payload={"model": "gemini-3.8-flash"},
        tokens_in=1200,
        tokens_out=60,
        cost_usd=0.000216,
        is_off_hours=True,
        timestamp=t0,
    )

    assert event.id > 0
    assert event.tenant_id == "magic-tour"
    assert event.tokens_in == 1200
    assert event.tokens_out == 60
    assert event.is_off_hours is True
