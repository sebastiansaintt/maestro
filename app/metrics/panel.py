from __future__ import annotations

import html

from app.metrics.service import TenantMetricsReport, render_markdown_report


def render_panel_html(report: TenantMetricsReport) -> str:
    md_text = html.escape(render_markdown_report(report))
    tenant_esc = html.escape(report.tenant_id)
    unanswered_items = (
        "".join(f"<li>{html.escape(q)}</li>" for q in report.unanswered_questions)
        or "<li>Sin preguntas pendientes</li>"
    )
    tours_items = (
        "".join(
            f"<li><code>{html.escape(slug)}</code>: <strong>{count}</strong></li>"
            for slug, count in report.top_tours.items()
        )
        or "<li>Sin tours registrados aún</li>"
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Panel Maestro — {tenant_esc}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; }}
    .container {{ max-width: 980px; margin: 0 auto; }}
    h1 {{ margin-top: 0; font-size: 1.75rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin: 1.5rem 0; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.25rem; }}
    .card .label {{ color: #94a3b8; font-size: 0.85rem; }}
    .card .value {{ font-size: 1.8rem; font-weight: 700; margin-top: 0.35rem; color: #38bdf8; }}
    textarea {{ width: 100%; min-height: 260px; background: #020617; color: #e2e8f0; border: 1px solid #334155; border-radius: 0.5rem; padding: 1rem; font-family: monospace; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🌴 Panel Operativo Maestro — <code>{tenant_esc}</code></h1>
    <div class="grid">
      <div class="card">
        <div class="label">🌙 % Fuera de Horario (18:00–08:00)</div>
        <div class="value">{report.off_hours_percentage}%</div>
      </div>
      <div class="card">
        <div class="label">Conversaciones Atendidas</div>
        <div class="value">{report.total_sessions}</div>
      </div>
      <div class="card">
        <div class="label">Clientes Calificados</div>
        <div class="value">{report.qualified_leads_count} ({report.qualification_rate_pct}%)</div>
      </div>
      <div class="card">
        <div class="label">Cuentas Despachadas / Comprobantes</div>
        <div class="value">{report.payment_info_sent_count} / {report.payment_receipts_count}</div>
      </div>
      <div class="card">
        <div class="label">Pases a Asesor Humano</div>
        <div class="value">{report.handoffs_total}</div>
      </div>
      <div class="card">
        <div class="label">Costo Acumulado Gemini (USD)</div>
        <div class="value">${report.cost_usd_total:.5f}</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Tours más consultados</h3>
        <ul>{tours_items}</ul>
      </div>
      <div class="card">
        <h3>Preguntas fuera de catálogo</h3>
        <ul>{unanswered_items}</ul>
      </div>
    </div>

    <div class="card">
      <h3>📋 Reporte Semanal en Markdown (Listo para enviar a Gerencia)</h3>
      <textarea readonly>{md_text}</textarea>
    </div>
  </div>
</body>
</html>"""
