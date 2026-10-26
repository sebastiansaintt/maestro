from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from app.db.repository import SessionRepository
from app.main import create_app


@pytest.mark.asyncio
async def test_reporte_and_panel_reject_unauthenticated_requests_and_serve_json_markdown_and_html(
    tmp_path: Path,
) -> None:
    repo = SessionRepository(db_path=tmp_path / "report_endpoint.db")
    await repo.init_db()

    t_night = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)
    session, _ = await repo.get_or_create_active_session("magic-tour", "573001112233", now=t_night)
    await repo.update_session(
        session.id,
        reservation_data={
            "name": "Andrés",
            "tour_slug": "parque-tayrona",
            "people": 3,
            "date": "10 dic",
        },
    )
    await repo.record_event(
        tenant_id="magic-tour",
        session_id=session.id,
        event_type="TOKEN_USAGE",
        tokens_in=1000,
        tokens_out=40,
        cost_usd=0.000174,
        is_off_hours=True,
        timestamp=t_night,
    )

    app = create_app(
        repository=repo,
        report_api_token="secret_admin_token_2026",
    )
    client = TestClient(app)

    # 1. Missing or wrong token -> 401 Unauthorized
    assert client.get("/reporte?tenant_id=magic-tour").status_code == 401
    assert (
        client.get(
            "/reporte?tenant_id=magic-tour",
            headers={"x-report-token": "wrong_token"},
        ).status_code
        == 401
    )
    assert client.get("/panel?tenant_id=magic-tour").status_code == 401

    # 2. Valid token + JSON format
    r_json = client.get(
        "/reporte?tenant_id=magic-tour&format=json",
        headers={"x-report-token": "secret_admin_token_2026"},
    )
    assert r_json.status_code == 200
    data = r_json.json()
    assert data["tenant_id"] == "magic-tour"
    assert data["total_sessions"] == 1
    assert data["off_hours_percentage"] == 100.0
    assert data["qualified_leads_count"] == 1

    # 3. Valid token + Markdown format
    r_md = client.get(
        "/reporte?tenant_id=magic-tour&format=markdown",
        headers={"x-report-token": "secret_admin_token_2026"},
    )
    assert r_md.status_code == 200
    assert "Reporte Semanal Maestro" in r_md.text
    assert "100.0%" in r_md.text

    # 4. Valid token + HTML Panel
    r_panel = client.get(
        "/panel?tenant_id=magic-tour&token=secret_admin_token_2026",
    )
    assert r_panel.status_code == 200
    assert "text/html" in r_panel.headers["content-type"]
    assert "magic-tour" in r_panel.text
    assert "100.0%" in r_panel.text
