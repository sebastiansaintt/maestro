from __future__ import annotations

from dataclasses import dataclass
import re

from app.core.logging import get_logger

logger = get_logger()

MAX_INPUT_CHARS = 1200
MAX_AUDIO_SECONDS = 90  # 03_SEGURIDAD_IA.md §4 (Denial of Wallet)

OFFICIAL_PRICE_POLICY_FALLBACK = (
    "Nuestras tarifas oficiales son las publicadas. Cualquier condición especial "
    "debe ser confirmada y tramitada directamente por un asesor humano."
)

GENERAL_SAFE_FALLBACK = (
    "Soy el asistente virtual de reservas de Magic Tour Colombia. "
    "¿En qué tour o destino de Santa Marta, Parque Tayrona, Ciudad Perdida o La Guajira te puedo asesorar hoy?"
)

_DELIMITER_TAG_RE = re.compile(r"</?\s*MENSAJE_USUARIO\s*>", re.IGNORECASE)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"olvida\s+(todas\s+)?tus\s+instrucciones", re.IGNORECASE),
    re.compile(r"ignora\s+(todas\s+)?(las\s+)?instrucciones", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"act[uú]a\s+como\s+(un\s+)?(desarrollador|dan|admin)", re.IGNORECASE),
    re.compile(r"(pagos|config)\.yaml", re.IGNORECASE),
    re.compile(r"\.env\b", re.IGNORECASE),
)

_PRICE_GASLIGHTING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(habl[eé]\s+con\s+el\s+(gerente|due[ñn]o)|me\s+lo\s+dej[oó]\s+a\s+mitad)", re.IGNORECASE),
    re.compile(r"mitad\s+de\s+precio", re.IGNORECASE),
    re.compile(r"me\s+(prometieron|dieron|autorizaron)\s+un\s+descuento", re.IGNORECASE),
)

_LEAKED_SECRET_OR_TAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(config|pagos|pagos\.example)\.yaml\b", re.IGNORECASE),
    re.compile(r"(?<!\w)\.env\b", re.IGNORECASE),
    re.compile(r"</?\s*(MENSAJE_USUARIO|BASE_DE_CONOCIMIENTO|DIRECTIVAS_SISTEMA)\s*>", re.IGNORECASE),
    re.compile(r"GEMINI_API_KEY|EVOLUTION_API_KEY", re.IGNORECASE),
)

_UNAUTHORIZED_DISCOUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bte\s+(dejo|doy|aplico|hago)\s+(el\s+tour\s+a\s+mitad\s+de\s+precio|un\s+\d+\s*%\s+de\s+descuento)", re.IGNORECASE),
    re.compile(r"\ba\s+mitad\s+de\s+precio\s+con\s+\d+\s*%\s+de\s+descuento\b", re.IGNORECASE),
    re.compile(r"\bte\s+rebajo\s+el\s+precio\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class InputGuardResult:
    clean_text: str
    wrapped_text: str
    exceeded_length: bool
    injection_suspected: bool
    price_gaslighting_suspected: bool


@dataclass(frozen=True)
class OutputGuardResult:
    approved: bool
    safe_text: str
    violation_reason: str | None = None


def sanitize_user_input(
    raw_text: str,
    max_chars: int = MAX_INPUT_CHARS,
) -> InputGuardResult:
    text = (raw_text or "").strip()

    # Check if raw text attempts to break out of XML delimiter before stripping
    delimiter_breakout_attempted = bool(_DELIMITER_TAG_RE.search(text))
    text_no_tags = _DELIMITER_TAG_RE.sub("", text).strip()

    exceeded_length = len(text_no_tags) > max_chars
    clean_text = text_no_tags[:max_chars] if exceeded_length else text_no_tags

    injection_suspected = delimiter_breakout_attempted or any(
        p.search(clean_text) for p in _INJECTION_PATTERNS
    )
    price_gaslighting_suspected = any(
        p.search(clean_text) for p in _PRICE_GASLIGHTING_PATTERNS
    )

    if exceeded_length or injection_suspected or price_gaslighting_suspected:
        logger.warning(
            "input_guard_triggered",
            exceededLength=exceeded_length,
            injectionSuspected=injection_suspected,
            priceGaslightingSuspected=price_gaslighting_suspected,
            originalLength=len(text),
        )

    wrapped_text = f"<MENSAJE_USUARIO>\n{clean_text}\n</MENSAJE_USUARIO>"
    return InputGuardResult(
        clean_text=clean_text,
        wrapped_text=wrapped_text,
        exceeded_length=exceeded_length,
        injection_suspected=injection_suspected,
        price_gaslighting_suspected=price_gaslighting_suspected,
    )


def validate_bot_output(reply_text: str) -> OutputGuardResult:
    candidate = (reply_text or "").strip()

    if any(p.search(candidate) for p in _LEAKED_SECRET_OR_TAG_PATTERNS):
        logger.warning(
            "output_guard_triggered",
            reason="internal_secret_or_prompt_leak",
        )
        return OutputGuardResult(
            approved=False,
            safe_text=GENERAL_SAFE_FALLBACK,
            violation_reason="internal_secret_or_prompt_leak",
        )

    if any(p.search(candidate) for p in _UNAUTHORIZED_DISCOUNT_PATTERNS):
        logger.warning(
            "output_guard_triggered",
            reason="unauthorized_discount_commitment",
        )
        return OutputGuardResult(
            approved=False,
            safe_text=OFFICIAL_PRICE_POLICY_FALLBACK,
            violation_reason="unauthorized_discount_commitment",
        )

    return OutputGuardResult(
        approved=True,
        safe_text=candidate,
        violation_reason=None,
    )
