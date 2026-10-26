from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger
from app.knowledge.catalog import TourCatalogEntry

logger = get_logger()

# Orden fijo del checklist de pago (decisión de producto, Fase 4)
CHECKLIST_ORDER: tuple[str, ...] = ("name", "tour", "people", "date", "price_quoted")

_LABELS = {
    "es": {
        "name": "tu nombre",
        "tour": "el tour que te interesa",
        "people": "cuántas personas viajan",
        "date": "la fecha tentativa del viaje",
    },
    "en": {
        "name": "your name",
        "tour": "which tour you are interested in",
        "people": "how many people are traveling",
        "date": "your tentative travel date",
    },
}


@dataclass(frozen=True)
class PaymentAccount:
    method: str
    number: str


@dataclass(frozen=True)
class PaymentConfig:
    is_demo: bool
    holder_name: str
    nit: str
    accounts: tuple[PaymentAccount, ...]
    instructions_es: str
    instructions_en: str


@dataclass(frozen=True)
class PaymentGateResult:
    allowed: bool
    missing: list[str] = field(default_factory=list)


def load_payment_config(
    tenant_id: str,
    tenants_root: Path = Path("tenants"),
) -> PaymentConfig:
    """Lee pagos.yaml (gitignored). Si no existe, usa pagos.example.yaml (DEMO)."""
    tenant_dir = tenants_root / tenant_id
    real_path = tenant_dir / "pagos.yaml"
    path = real_path if real_path.exists() else tenant_dir / "pagos.example.yaml"
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    if path != real_path:
        logger.warning("payment_config_demo_fallback", tenantId=tenant_id)

    return PaymentConfig(
        is_demo=bool(raw.get("is_demo", path != real_path)),
        holder_name=str(raw.get("holder_name") or ""),
        nit=str(raw.get("nit") or ""),
        accounts=tuple(
            PaymentAccount(method=str(a.get("method")), number=str(a.get("number")))
            for a in raw.get("accounts") or []
        ),
        instructions_es=str(raw.get("instructions_es") or ""),
        instructions_en=str(raw.get("instructions_en") or ""),
    )


def format_payment_message(config: PaymentConfig, language: str = "es") -> str:
    accounts = "\n".join(f"• {a.method}: {a.number}" for a in config.accounts)
    if language == "en":
        return (
            "Great! 🌴 Here are our official payment details:\n\n"
            f"Account holder: {config.holder_name}\nNIT: {config.nit}\n{accounts}\n\n"
            f"{config.instructions_en}\n\n"
            "If you prefer to speak with an advisor before paying, just write *advisor*."
        )
    return (
        "¡Perfecto! 🌴 Estos son nuestros datos oficiales de pago:\n\n"
        f"Titular: {config.holder_name}\nNIT: {config.nit}\n{accounts}\n\n"
        f"{config.instructions_es}\n\n"
        "Si prefieres hablar con un asesor antes de pagar, escríbeme *asesor*."
    )


def evaluate_payment_gate(
    lead: dict[str, Any],
    catalog: dict[str, TourCatalogEntry],
    assistant_messages: list[str],
) -> PaymentGateResult:
    """Checklist determinista: la IA puede proponer 'quiere pagar', pero el código decide."""
    missing: list[str] = []
    tour = catalog.get(str(lead.get("tour_slug") or ""))

    if not str(lead.get("name") or "").strip():
        missing.append("name")
    if tour is None:
        missing.append("tour")
    if not lead.get("people"):
        missing.append("people")
    if not str(lead.get("date") or "").strip():
        missing.append("date")

    price_quoted = (
        tour is not None
        and tour.price_text is not None
        and any(tour.price_text in msg for msg in assistant_messages)
    )
    if not price_quoted:
        missing.append("price_quoted")

    return PaymentGateResult(allowed=not missing, missing=missing)


def build_payment_gate_message(
    missing: list[str],
    lead: dict[str, Any],
    catalog: dict[str, TourCatalogEntry],
    language: str = "es",
    blocked_attempts: int = 1,
) -> str:
    lang = "en" if language == "en" else "es"
    labels = _LABELS[lang]
    asks = [labels[item] for item in CHECKLIST_ORDER if item in missing and item in labels]
    tour = catalog.get(str(lead.get("tour_slug") or ""))

    lines: list[str] = []
    if lang == "en":
        lines.append("Happy to help you with the payment! 🌴")
    else:
        lines.append("¡Con gusto te ayudo con el pago! 🌴")

    if "price_quoted" in missing and tour is not None and tour.price_text:
        if lang == "en":
            lines.append(f"The published price for *{tour.title}* is {tour.price_text} COP.")
        else:
            lines.append(f"El valor publicado de *{tour.title}* es {tour.price_text} COP.")

    if asks:
        if lang == "en":
            lines.append("Before sharing the payment details, please tell me:")
        else:
            lines.append("Antes de compartirte los datos de pago, cuéntame por favor:")
        lines.extend(f"• {a}" for a in asks)
    elif lang == "en":
        lines.append("Would you like to continue with the payment?")
    else:
        lines.append("¿Confirmas que deseas continuar con el pago?")

    if blocked_attempts >= 2:
        if lang == "en":
            lines.append("If you prefer, I can connect you with a human advisor; just write *advisor*.")
        else:
            lines.append("Si prefieres, te paso con un asesor humano; solo escríbeme *asesor*.")

    return "\n".join(lines)
