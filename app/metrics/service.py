from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from app.db.repository import SessionRepository


@dataclass(frozen=True)
class TenantMetricsReport:
    tenant_id: str
    total_sessions: int
    total_turns: int
    off_hours_turns: int
    off_hours_percentage: float
    qualified_leads_count: int
    qualification_rate_pct: float
    payment_info_sent_count: int
    payment_receipts_count: int
    handoffs_total: int
    handoffs_by_reason: dict[str, int] = field(default_factory=dict)
    language_distribution: dict[str, int] = field(default_factory=dict)
    top_tours: dict[str, int] = field(default_factory=dict)
    tokens_in_total: int = 0
    tokens_out_total: int = 0
    cost_usd_total: float = 0.0
    avg_cost_per_session_usd: float = 0.0
    unanswered_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_qualified_lead(lead: dict[str, Any]) -> bool:
    return bool(
        str(lead.get("name") or "").strip()
        and str(lead.get("tour_slug") or lead.get("tour") or "").strip()
        and lead.get("people")
        and str(lead.get("date") or "").strip()
    )


class MetricsService:
    """Computes business KPIs (for the agency) and operational telemetry/costs (for Sebastián)."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    async def build_tenant_report(self, tenant_id: str) -> TenantMetricsReport:
        sessions = await self._repository.list_sessions(tenant_id)
        events = await self._repository.list_events(tenant_id)

        total_sessions = len(sessions)
        qualified_count = 0
        lang_counter: Counter[str] = Counter()
        tour_counter: Counter[str] = Counter()

        for s in sessions:
            lead = s.reservation_data or {}
            if _is_qualified_lead(lead):
                qualified_count += 1
            lang = "en" if str(lead.get("language") or "es").lower() == "en" else "es"
            lang_counter[lang] += 1
            tour = str(lead.get("tour_slug") or lead.get("tour") or "").strip()
            if tour:
                tour_counter[tour] += 1

        qualification_rate = (
            round((qualified_count / total_sessions) * 100.0, 1)
            if total_sessions > 0
            else 0.0
        )

        token_events = [e for e in events if e.event_type == "TOKEN_USAGE"]
        total_turns = len(token_events)
        off_hours_turns = sum(1 for e in token_events if e.is_off_hours)
        off_hours_pct = (
            round((off_hours_turns / total_turns) * 100.0, 1)
            if total_turns > 0
            else 0.0
        )

        payment_info_count = sum(1 for e in events if e.event_type == "PAYMENT_INFO")
        payment_receipts_count = sum(
            1 for e in events if e.event_type == "PAYMENT_RECEIPT"
        )

        handoff_events = [e for e in events if e.event_type == "HANDOFF"]
        reason_counter: Counter[str] = Counter()
        for he in handoff_events:
            reason = str((he.payload or {}).get("reason") or "other")
            reason_counter[reason] += 1

        tokens_in = sum(e.tokens_in for e in token_events)
        tokens_out = sum(e.tokens_out for e in token_events)
        cost_usd = round(sum(e.cost_usd for e in token_events), 6)
        avg_cost = (
            round(cost_usd / total_sessions, 6) if total_sessions > 0 else 0.0
        )

        unanswered = [
            str((e.payload or {}).get("question") or "").strip()
            for e in events
            if e.event_type == "UNANSWERED_QUESTION"
            and str((e.payload or {}).get("question") or "").strip()
        ]

        return TenantMetricsReport(
            tenant_id=tenant_id,
            total_sessions=total_sessions,
            total_turns=total_turns,
            off_hours_turns=off_hours_turns,
            off_hours_percentage=off_hours_pct,
            qualified_leads_count=qualified_count,
            qualification_rate_pct=qualification_rate,
            payment_info_sent_count=payment_info_count,
            payment_receipts_count=payment_receipts_count,
            handoffs_total=len(handoff_events),
            handoffs_by_reason=dict(reason_counter),
            language_distribution=dict(lang_counter),
            top_tours=dict(tour_counter.most_common(10)),
            tokens_in_total=tokens_in,
            tokens_out_total=tokens_out,
            cost_usd_total=cost_usd,
            avg_cost_per_session_usd=avg_cost,
            unanswered_questions=unanswered,
        )


def render_markdown_report(report: TenantMetricsReport) -> str:
    tours_lines = (
        "\n".join(f"  - `{slug}`: {count}" for slug, count in report.top_tours.items())
        or "  - Sin tours registrados aún"
    )
    handoff_lines = (
        "\n".join(
            f"  - `{reason}`: {count}"
            for reason, count in report.handoffs_by_reason.items()
        )
        or "  - Ninguno"
    )
    unanswered_lines = (
        "\n".join(f"  - {q}" for q in report.unanswered_questions)
        or "  - Ninguna pregunta pendiente"
    )

    return f"""# 📊 Reporte Semanal Maestro — `{report.tenant_id}`

## 1. Métricas Comerciales para la Agencia
- **Conversaciones atendidas:** {report.total_sessions}
- **🌙 Atención fuera de horario (6:00 p. m. – 8:00 a. m.):** **{report.off_hours_percentage}%** ({report.off_hours_turns} de {report.total_turns} turnos)
- **Clientes calificados (nombre + tour + fecha + personas):** {report.qualified_leads_count} ({report.qualification_rate_pct}%)
- **Datos de cuenta despachados ( intención de pago directo):** {report.payment_info_sent_count}
- **Comprobantes de pago recibidos:** {report.payment_receipts_count}
- **Pases a asesor humano:** {report.handoffs_total}
{handoff_lines}
- **Idiomas:** Español: {report.language_distribution.get('es', 0)} | Inglés: {report.language_distribution.get('en', 0)}
- **Tours más consultados:**
{tours_lines}

---

## 2. Operación y Costos (Consumo Interno)
- **Tokens de entrada:** {report.tokens_in_total:,}
- **Tokens de salida:** {report.tokens_out_total:,}
- **Costo acumulado Gemini (USD):** ${report.cost_usd_total:.6f} USD (Promedio por sesión: ${report.avg_cost_per_session_usd:.6f} USD)

## 3. Preguntas Fuera de Catálogo (Para Enriquecer Knowledge)
{unanswered_lines}
"""
