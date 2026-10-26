from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import aiosqlite

from app.core.logging import get_logger
from app.db.models import ContactRecord, EventRecord, MessageRecord, SessionRecord

logger = get_logger()

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    whatsapp_instance TEXT,
    advisor_group_id TEXT,
    activo INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    name TEXT,
    language TEXT DEFAULT 'es',
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, phone_number)
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    contact_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    reservation_data TEXT DEFAULT '{}',
    paused_until TEXT,
    last_activity TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (contact_id) REFERENCES contacts (id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    content TEXT NOT NULL,
    audio_transcription TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    session_id INTEGER,
    event_type TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    is_off_hours INTEGER DEFAULT 0,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contacts_tenant_phone ON contacts (tenant_id, phone_number);
CREATE INDEX IF NOT EXISTS idx_sessions_tenant_contact ON sessions (tenant_id, contact_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status_expires ON sessions (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id);
CREATE INDEX IF NOT EXISTS idx_events_tenant_time ON events (tenant_id, timestamp);
"""


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _from_iso(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str)


class SessionRepository:
    """Multi-tenant SQLite repository with WAL mode and 4-day session lifecycle."""

    def __init__(self, db_path: Path | str = Path("maestro.db")) -> None:
        self._db_path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA_SQL)
            await db.commit()
        logger.info("sqlite_db_initialized", dbPath=self._db_path, journalMode="WAL")

    async def get_contact_by_id(self, contact_id: int) -> ContactRecord | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return ContactRecord(
                id=row["id"],
                tenant_id=row["tenant_id"],
                phone_number=row["phone_number"],
                name=row["name"],
                language=row["language"],
                created_at=_from_iso(row["created_at"]),
            )

    async def get_or_create_contact(
        self,
        tenant_id: str,
        phone_number: str,
        name: str | None = None,
        now: datetime | None = None,
    ) -> ContactRecord:
        now_dt = now or datetime.now(timezone.utc)
        now_str = _to_iso(now_dt)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM contacts WHERE tenant_id = ? AND phone_number = ?",
                (tenant_id, phone_number),
            )
            row = await cursor.fetchone()
            if row is not None:
                return ContactRecord(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    phone_number=row["phone_number"],
                    name=row["name"],
                    language=row["language"],
                    created_at=_from_iso(row["created_at"]),
                )

            cursor = await db.execute(
                """
                INSERT INTO contacts (tenant_id, phone_number, name, language, created_at)
                VALUES (?, ?, ?, 'es', ?)
                """,
                (tenant_id, phone_number, name, now_str),
            )
            contact_id = cursor.lastrowid
            await db.commit()

            return ContactRecord(
                id=contact_id,
                tenant_id=tenant_id,
                phone_number=phone_number,
                name=name,
                language="es",
                created_at=now_dt,
            )

    async def get_or_create_active_session(
        self,
        tenant_id: str,
        phone_number: str,
        now: datetime | None = None,
    ) -> tuple[SessionRecord, bool]:
        now_dt = now or datetime.now(timezone.utc)
        now_str = _to_iso(now_dt)
        four_days_later = now_dt + timedelta(days=4)
        expires_str = _to_iso(four_days_later)

        contact = await self.get_or_create_contact(
            tenant_id=tenant_id,
            phone_number=phone_number,
            now=now_dt,
        )

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM sessions
                WHERE tenant_id = ? AND contact_id = ? AND status != 'ARCHIVADA'
                ORDER BY id DESC LIMIT 1
                """,
                (tenant_id, contact.id),
            )
            row = await cursor.fetchone()

            if row is not None:
                current_expires = _from_iso(row["expires_at"])
                if current_expires > now_dt:
                    # Active session (or returning from soft close)
                    new_status = "ACTIVA" if row["status"] == "CIERRE_SUAVE" else row["status"]
                    await db.execute(
                        """
                        UPDATE sessions
                        SET status = ?, last_activity = ?, expires_at = ?
                        WHERE id = ?
                        """,
                        (new_status, now_str, expires_str, row["id"]),
                    )
                    await db.commit()

                    return (
                        SessionRecord(
                            id=row["id"],
                            tenant_id=tenant_id,
                            contact_id=contact.id,
                            status=new_status,
                            reservation_data=json.loads(row["reservation_data"] or "{}"),
                            paused_until=_from_iso(row["paused_until"]) if row["paused_until"] else None,
                            last_activity=now_dt,
                            expires_at=four_days_later,
                        ),
                        False,
                    )
                else:
                    # Expired (>= 4 days idle) -> Archive old session (RF-18)
                    await db.execute(
                        "UPDATE sessions SET status = 'ARCHIVADA' WHERE id = ?",
                        (row["id"],),
                    )

            # Create brand new session
            cursor = await db.execute(
                """
                INSERT INTO sessions (
                    tenant_id, contact_id, status, reservation_data,
                    paused_until, last_activity, expires_at
                ) VALUES (?, ?, 'ACTIVA', '{}', NULL, ?, ?)
                """,
                (tenant_id, contact.id, now_str, expires_str),
            )
            session_id = cursor.lastrowid
            await db.commit()

            return (
                SessionRecord(
                    id=session_id,
                    tenant_id=tenant_id,
                    contact_id=contact.id,
                    status="ACTIVA",
                    reservation_data={},
                    paused_until=None,
                    last_activity=now_dt,
                    expires_at=four_days_later,
                ),
                True,
            )

    async def append_message(
        self,
        session_id: int,
        role: str,
        msg_type: str,
        content: str,
        audio_transcription: str | None = None,
        timestamp: datetime | None = None,
    ) -> MessageRecord:
        ts = timestamp or datetime.now(timezone.utc)
        ts_str = _to_iso(ts)

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO messages (session_id, role, msg_type, content, audio_transcription, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, msg_type, content, audio_transcription, ts_str),
            )
            msg_id = cursor.lastrowid
            await db.commit()

            return MessageRecord(
                id=msg_id,
                session_id=session_id,
                role=role,
                msg_type=msg_type,
                content=content,
                audio_transcription=audio_transcription,
                timestamp=ts,
            )

    async def list_session_messages(self, session_id: int) -> list[MessageRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [
                MessageRecord(
                    id=r["id"],
                    session_id=r["session_id"],
                    role=r["role"],
                    msg_type=r["msg_type"],
                    content=r["content"],
                    audio_transcription=r["audio_transcription"],
                    timestamp=_from_iso(r["timestamp"]),
                )
                for r in rows
            ]

    async def find_sessions_due_for_soft_close(
        self,
        now: datetime,
        idle_seconds: float = 600.0,
    ) -> list[SessionRecord]:
        threshold = now - timedelta(seconds=idle_seconds)
        now_str = _to_iso(now)
        threshold_str = _to_iso(threshold)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM sessions
                WHERE status = 'ACTIVA' AND last_activity <= ? AND expires_at > ?
                ORDER BY id ASC
                """,
                (threshold_str, now_str),
            )
            rows = await cursor.fetchall()
            return [
                SessionRecord(
                    id=r["id"],
                    tenant_id=r["tenant_id"],
                    contact_id=r["contact_id"],
                    status=r["status"],
                    reservation_data=json.loads(r["reservation_data"] or "{}"),
                    paused_until=_from_iso(r["paused_until"]) if r["paused_until"] else None,
                    last_activity=_from_iso(r["last_activity"]),
                    expires_at=_from_iso(r["expires_at"]),
                )
                for r in rows
            ]

    async def mark_soft_closed(self, session_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE sessions SET status = 'CIERRE_SUAVE' WHERE id = ?",
                (session_id,),
            )
            await db.commit()

    async def get_session(self, session_id: int) -> SessionRecord:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            )
            r = await cursor.fetchone()
            if r is None:
                raise KeyError(f"Session {session_id} not found")
            return SessionRecord(
                id=r["id"],
                tenant_id=r["tenant_id"],
                contact_id=r["contact_id"],
                status=r["status"],
                reservation_data=json.loads(r["reservation_data"] or "{}"),
                paused_until=_from_iso(r["paused_until"]) if r["paused_until"] else None,
                last_activity=_from_iso(r["last_activity"]),
                expires_at=_from_iso(r["expires_at"]),
            )

    async def update_session(
        self,
        session_id: int,
        *,
        status: str | None = None,
        reservation_data: dict | None = None,
        paused_until: datetime | None = None,
        clear_pause: bool = False,
    ) -> SessionRecord:
        current = await self.get_session(session_id)
        new_status = status if status is not None else current.status
        new_data = (
            reservation_data
            if reservation_data is not None
            else current.reservation_data
        )
        if clear_pause:
            new_paused_str: str | None = None
        elif paused_until is not None:
            new_paused_str = _to_iso(paused_until)
        else:
            new_paused_str = (
                _to_iso(current.paused_until) if current.paused_until else None
            )

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                UPDATE sessions
                SET status = ?, reservation_data = ?, paused_until = ?
                WHERE id = ?
                """,
                (
                    new_status,
                    json.dumps(new_data, ensure_ascii=False),
                    new_paused_str,
                    session_id,
                ),
            )
            await db.commit()
        return await self.get_session(session_id)

    async def list_sessions(self, tenant_id: str) -> list[SessionRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE tenant_id = ? ORDER BY id ASC",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
            return [
                SessionRecord(
                    id=r["id"],
                    tenant_id=r["tenant_id"],
                    contact_id=r["contact_id"],
                    status=r["status"],
                    reservation_data=json.loads(r["reservation_data"] or "{}"),
                    paused_until=_from_iso(r["paused_until"]) if r["paused_until"] else None,
                    last_activity=_from_iso(r["last_activity"]),
                    expires_at=_from_iso(r["expires_at"]),
                )
                for r in rows
            ]

    async def list_events(
        self,
        tenant_id: str,
        event_type: str | None = None,
    ) -> list[EventRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if event_type:
                cursor = await db.execute(
                    "SELECT * FROM events WHERE tenant_id = ? AND event_type = ? ORDER BY id ASC",
                    (tenant_id, event_type),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM events WHERE tenant_id = ? ORDER BY id ASC",
                    (tenant_id,),
                )
            rows = await cursor.fetchall()
            return [
                EventRecord(
                    id=r["id"],
                    tenant_id=r["tenant_id"],
                    session_id=r["session_id"],
                    event_type=r["event_type"],
                    payload=json.loads(r["payload"] or "{}"),
                    tokens_in=r["tokens_in"],
                    tokens_out=r["tokens_out"],
                    cost_usd=r["cost_usd"],
                    is_off_hours=bool(r["is_off_hours"]),
                    timestamp=_from_iso(r["timestamp"]),
                )
                for r in rows
            ]

    async def record_event(
        self,
        tenant_id: str,
        session_id: int | None,
        event_type: str,
        payload: dict | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        is_off_hours: bool = False,
        timestamp: datetime | None = None,
    ) -> EventRecord:
        ts = timestamp or datetime.now(timezone.utc)
        ts_str = _to_iso(ts)
        payload_str = json.dumps(payload or {})

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO events (
                    tenant_id, session_id, event_type, payload,
                    tokens_in, tokens_out, cost_usd, is_off_hours, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    session_id,
                    event_type,
                    payload_str,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    1 if is_off_hours else 0,
                    ts_str,
                ),
            )
            event_id = cursor.lastrowid
            await db.commit()

            return EventRecord(
                id=event_id,
                tenant_id=tenant_id,
                session_id=session_id,
                event_type=event_type,
                payload=payload or {},
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                is_off_hours=is_off_hours,
                timestamp=ts,
            )
