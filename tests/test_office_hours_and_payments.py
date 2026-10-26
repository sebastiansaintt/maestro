from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.conversation.office_hours import OfficeHoursPolicy
from app.conversation.payments import (
    build_payment_gate_message,
    evaluate_payment_gate,
    format_payment_message,
    load_payment_config,
)
from app.knowledge.catalog import load_tour_catalog

BOGOTA = ZoneInfo("America/Bogota")


def test_office_hours_8am_to_6pm_every_day_including_sundays_and_holidays() -> None:
    policy = OfficeHoursPolicy(timezone="America/Bogota", start_hour=8, end_hour=18)

    assert policy.is_off_hours(datetime(2026, 10, 21, 7, 59, tzinfo=BOGOTA)) is True
    assert policy.is_off_hours(datetime(2026, 10, 21, 8, 0, tzinfo=BOGOTA)) is False
    assert policy.is_off_hours(datetime(2026, 10, 21, 17, 59, tzinfo=BOGOTA)) is False
    assert policy.is_off_hours(datetime(2026, 10, 21, 18, 0, tzinfo=BOGOTA)) is True
    # Domingo y festivo (2 nov 2026, Todos los Santos) mantienen el mismo horario
    assert policy.is_off_hours(datetime(2026, 10, 25, 10, 0, tzinfo=BOGOTA)) is False
    assert policy.is_off_hours(datetime(2026, 11, 2, 10, 0, tzinfo=BOGOTA)) is False
    # UTC 23:30 = 18:30 en Bogotá -> fuera de horario
    assert policy.is_off_hours(datetime(2026, 10, 21, 23, 30, tzinfo=timezone.utc)) is True


def _write_tenant(tmp_path: Path, with_real_pagos: bool) -> Path:
    tenant = tmp_path / "magic-tour"
    (tenant / "knowledge" / "tours").mkdir(parents=True)
    (tenant / "pagos.example.yaml").write_text(
        'is_demo: true\n'
        'holder_name: "MAGIC TOUR (DEMO)"\n'
        'nit: "900.000.000-0 (DEMO)"\n'
        'accounts:\n'
        '  - method: "Nequi"\n'
        '    number: "300 000 0000 (DEMO)"\n'
        'instructions_es: "Envíanos el comprobante por este chat."\n'
        'instructions_en: "Send us the receipt in this chat."\n',
        encoding="utf-8",
    )
    if with_real_pagos:
        (tenant / "pagos.yaml").write_text(
            'is_demo: false\n'
            'holder_name: "MAGIC TOUR REAL"\n'
            'nit: "901.111.111-1"\n'
            'accounts:\n'
            '  - method: "Bancolombia Ahorros"\n'
            '    number: "123-456789-00"\n'
            'instructions_es: "Envíanos el comprobante por este chat."\n'
            'instructions_en: "Send us the receipt in this chat."\n',
            encoding="utf-8",
        )
    (tenant / "knowledge" / "tours" / "ciudad-perdida-santa-marta-4-dias.md").write_text(
        "# Ciudad Perdida Santa Marta Tour 4 días\n"
        "- Destino: Ciudad Perdida Colombia | Tipo: tour\n"
        "- Precio: 1.860.000 COP (Reserva con: 10%)\n",
        encoding="utf-8",
    )
    return tmp_path


def test_payment_config_prefers_real_file_and_falls_back_to_demo(tmp_path: Path) -> None:
    demo_root = _write_tenant(tmp_path / "demo", with_real_pagos=False)
    demo = load_payment_config("magic-tour", tenants_root=demo_root)
    assert demo.is_demo is True
    assert demo.accounts[0].number == "300 000 0000 (DEMO)"

    real_root = _write_tenant(tmp_path / "real", with_real_pagos=True)
    real = load_payment_config("magic-tour", tenants_root=real_root)
    assert real.is_demo is False
    assert real.accounts[0].method == "Bancolombia Ahorros"

    msg_es = format_payment_message(real, language="es")
    assert "MAGIC TOUR REAL" in msg_es
    assert "Bancolombia Ahorros: 123-456789-00" in msg_es
    assert "Envíanos el comprobante por este chat." in msg_es
    assert "asesor" in msg_es  # siempre se ofrece el camino alterno (RF-09)

    msg_en = format_payment_message(real, language="en")
    assert "Send us the receipt in this chat." in msg_en
    assert "advisor" in msg_en


def test_payment_gate_blocks_until_checklist_is_complete_and_price_was_quoted(
    tmp_path: Path,
) -> None:
    root = _write_tenant(tmp_path, with_real_pagos=False)
    catalog = load_tour_catalog("magic-tour", tenants_root=root)
    assert catalog["ciudad-perdida-santa-marta-4-dias"].price_text == "1.860.000"

    # Atacante que pide la cuenta de entrada: falta todo
    empty = evaluate_payment_gate(lead={}, catalog=catalog, assistant_messages=[])
    assert empty.allowed is False
    assert empty.missing == ["name", "tour", "people", "date", "price_quoted"]

    # Tour inexistente en el catálogo no cuenta como tour elegido
    fake_tour = evaluate_payment_gate(
        lead={"name": "Laura", "tour_slug": "hotel-cartagena", "people": 2, "date": "15 nov"},
        catalog=catalog,
        assistant_messages=[],
    )
    assert fake_tour.missing == ["tour", "price_quoted"]

    full_lead = {
        "name": "Laura",
        "tour_slug": "ciudad-perdida-santa-marta-4-dias",
        "people": 2,
        "date": "15 de noviembre",
    }
    # Datos completos pero el bot nunca informó el precio
    not_quoted = evaluate_payment_gate(
        lead=full_lead, catalog=catalog, assistant_messages=["¡Hola Laura!"]
    )
    assert not_quoted.missing == ["price_quoted"]

    allowed = evaluate_payment_gate(
        lead=full_lead,
        catalog=catalog,
        assistant_messages=["El tour de 4 días cuesta 1.860.000 COP."],
    )
    assert allowed.allowed is True
    assert allowed.missing == []


def test_payment_gate_message_asks_missing_items_and_offers_advisor_on_insistence(
    tmp_path: Path,
) -> None:
    root = _write_tenant(tmp_path, with_real_pagos=False)
    catalog = load_tour_catalog("magic-tour", tenants_root=root)

    first = build_payment_gate_message(
        missing=["name", "tour", "people", "date", "price_quoted"],
        lead={},
        catalog=catalog,
        language="es",
        blocked_attempts=1,
    )
    assert "nombre" in first
    assert "tour" in first
    assert "300 000 0000" not in first  # jamás expone cuentas
    assert "asesor" not in first

    # Con el tour ya elegido, el mensaje informa el precio publicado
    with_tour = build_payment_gate_message(
        missing=["price_quoted"],
        lead={"tour_slug": "ciudad-perdida-santa-marta-4-dias"},
        catalog=catalog,
        language="es",
        blocked_attempts=1,
    )
    assert "1.860.000 COP" in with_tour

    insisting = build_payment_gate_message(
        missing=["name"],
        lead={},
        catalog=catalog,
        language="en",
        blocked_attempts=3,
    )
    assert "name" in insisting
    assert "advisor" in insisting
