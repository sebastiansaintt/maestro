from datetime import datetime, timezone
from pathlib import Path
import pytest

from app.db.repository import SessionRepository
from app.metrics.service import MetricsService, render_markdown_report


@pytest.mark.asyncio
async def test_metrics_service_computes_business_and_operational_kpis_and_renders_markdown(
    tmp_path: Path,
) -> None:
    repo = SessionRepository(db_path=tmp_path / "metrics.db")
    await repo.init_db()

    t_day = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)    # 10:00 Bogota (office hours)
    t_night = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)   # 21:00 Bogota (off hours)

    # Session 1: Qualified lead in Spanish, received payment info and sent receipt at night
    s1, _ = await repo.get_or_create_active_session("magic-tour", "573001112233", now=t_day)
    await repo.update_session(
        s1.id,
        status="ESPERANDO_ASESOR",
        reservation_data={
            "name": "Laura Gómez",
            "tour_slug": "ciudad-perdida-santa-marta-4-dias",
            "people": 2,
            "date": "15 de noviembre",
            "origin": "Medellín",
            "language": "es",
        },
    )
    await repo.record_event(
        tenant_id="magic-tour",
        session_id=s1.id,
        event_type="TOKEN_USAGE",
        payload={"model": "gemini-3.8-flash", "kind": "conversation"},
        tokens_in=2000,
        tokens_out=100,
        cost_usd=0.00036,
        is_off_hours=False,
        timestamp=t_day,
    )
    await repo.record_event(
        tenant_id="magic-tour",
        session_id=s1.id,
        event_type="PAYMENT_INFO",
        payload={"is_demo": True},
        is_off_hours=True,
        timestamp=t_night,
    )
    await repo.record_event(
        tenant_id="magic-tour",
        session_id=s1.id,
        event_type="PAYMENT_RECEIPT",
        payload={"media_kind": "image"},
        is_off_hours=True,
        timestamp=t_night,
    )
    await repo.record_event(
        tenant_id="magic-tour",
        session_id=s1.id,
        event_type="HANDOFF",
        payload={"reason": "payment_receipt", "high_priority": True},
        is_off_hours=True,
        timestamp=t_night,
    )

    # Session 2: Unqualified inquiry in English at night with an unanswered question
    s2, _ = await repo.get_or_create_active_session("magic-tour", "15559998877", now=t_night)
    await repo.update_session(
        s2.id,
        reservation_data={"language": "en"},
    )
    await repo.record_event(
        tenant_id="magic-tour",
        session_id=s2.id,
        event_type="TOKEN_USAGE",
        payload={"model": "gemini-3.8-flash", "kind": "conversation"},
        tokens_in=1500,
        tokens_out=50,
        cost_usd=0.000255,
        is_off_hours=True,
        timestamp=t_night,
    )
    await repo.record_event(
        tenant_id="magic-tour",
        session_id=s2.id,
        event_type="UNANSWERED_QUESTION",
        payload={"question": "Do you sell domestic flights to Bogota?"},
        is_off_hours=True,
        timestamp=t_night,
    )

    svc = MetricsService(repository=repo)
    report = await svc.build_tenant_report("magic-tour")

    assert report.tenant_id == "magic-tour"
    assert report.total_sessions == 2
    assert report.qualified_leads_count == 1
    assert report.qualification_rate_pct == 50.0
    # 2 TOKEN_USAGE turns: 1 day, 1 night -> 50.0% off-hours
    assert report.off_hours_percentage == 50.0
    assert report.payment_info_sent_count == 1
    assert report.payment_receipts_count == 1
    assert report.handoffs_total == 1
    assert report.handoffs_by_reason == {"payment_receipt": 1}
    assert report.tokens_in_total == 3500
    assert report.tokens_out_total == 150
    assert report.cost_usd_total == pytest.approx(0.000615, rel=1e-4)
    assert report.unanswered_questions == ["Do you sell domestic flights to Bogota?"]
    assert report.language_distribution == {"es": 1, "en": 1}
    assert report.top_tours == {"ciudad-perdida-santa-marta-4-dias": 1}

    md = render_markdown_report(report)
    assert "Reporte Semanal Maestro" in md
    assert "50.0%" in md
    assert "ciudad-perdida-santa-marta-4-dias" in md
    assert "Do you sell domestic flights to Bogota?" in md
