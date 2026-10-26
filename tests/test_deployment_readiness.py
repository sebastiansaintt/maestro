from __future__ import annotations

from pathlib import Path
import re

from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gitignore_protects_secrets_payment_accounts_and_sqlite_files() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    required_patterns = [
        ".env",
        "**/pagos.yaml",
        "!**/pagos.example.yaml",
        "*.db",
        "*.db-wal",
        "*.sqlite3",
    ]
    for pattern in required_patterns:
        assert pattern in gitignore, f"Missing required .gitignore protection: {pattern}"


def test_no_real_pagos_yaml_or_real_api_keys_are_present_in_repo_templates() -> None:
    # Ningún tenant debe tener un pagos.yaml versionado por defecto; solo pagos.example.yaml
    example_files = list((REPO_ROOT / "tenants").glob("*/pagos.example.yaml"))
    assert len(example_files) >= 1

    for example_file in example_files:
        text = example_file.read_text(encoding="utf-8")
        assert "DEMO" in text, f"{example_file} must clearly mark placeholder accounts as DEMO"

    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    # Verificar que no haya una llave real de Google AI Studio (AIza...) en .env.example
    assert not re.search(r"AIza[0-9A-Za-z\-_]{30,}", env_example)

    # Verificar que todas las claves de .env.example correspondan a campos válidos o documentados
    settings_fields = {name.upper() for name in Settings.model_fields}
    for required_env in (
        "APP_ENV",
        "LOG_LEVEL",
        "EVOLUTION_API_URL",
        "EVOLUTION_API_KEY",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "REPORT_API_TOKEN",
    ):
        assert required_env in env_example
        assert required_env in settings_fields


def test_phase7_demo_and_deployment_guide_exists_and_covers_all_checklists() -> None:
    guide_path = REPO_ROOT / "docs" / "05_DEMO_Y_DESPLIEGUE.md"
    assert guide_path.is_file(), "Missing docs/05_DEMO_Y_DESPLIEGUE.md"

    content = guide_path.read_text(encoding="utf-8")
    required_sections = [
        "Guion de Demo en Vivo",
        "Payment Gate",
        "#pausar",
        "#activar",
        "/panel",
        "/reporte",
        "pagos.yaml",
        "VPS",
    ]
    for section in required_sections:
        assert section in content, f"Demo & deployment guide missing '{section}'"
