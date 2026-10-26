from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
import sys

# Ensure project root is in sys.path when invoked as python scripts/scrape.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.logging import configure_logging, get_logger
from app.knowledge.loader import TenantKnowledgeLoader
from app.knowledge.scraper import MagicTourScraper


def _build_official_contact_markdown(review_date: str) -> str:
    return f"""# Contacto Oficial y Canales de Atención — Magic Tour Colombia

## Identificación Legal y Registro Nacional de Turismo (RNT)
- **Razón Comercial:** Magic Tour Colombia
- **Registro Nacional de Turismo (RNT):** 30835
- **Sede Principal (Centro Histórico):** Calle 17 No. 3-69 Local 1, Centro Histórico, Santa Marta, Magdalena, Colombia (Código Postal 470004)
- **Oficina Operativa:** Calle 22 # 5-67, Santa Marta, Magdalena, Colombia
- **Sitio Web Oficial:** https://magictourcolombia.com/es

## Líneas Oficiales de Atención y WhatsApp
- **Reservas y Atención al Viajero (Línea 1):** +57 300 239 4612 (`https://wa.me/573002394612`)
- **Reservas y Atención al Viajero (Línea 2):** +57 317 679 2441 (`https://wa.me/573176792441`)
- **Turismo Corporativo y Alianzas:** +57 302 516 0413 (`https://wa.me/573025160413`)
- **Eventos, Convenciones y MICE:** +57 302 709 5344 (`https://wa.me/573027095344`)
- **Correo Electrónico Oficial de Reservas:** reservas@magictourcolombia.com

## Horarios de Atención de Asesores Humanos
- **Lunes a Domingo y Festivos:** 8:00 a. m. a 6:00 p. m. (Hora Colombia, `America/Bogota`)
- **Atención fuera de horario (6:00 p. m. a 8:00 a. m.):** Asistencia informativa automatizada por Maestro; las solicitudes de reserva, verificación de cupos o validación de comprobantes recibidas en horario nocturno son gestionadas de forma prioritaria a partir de las 8:00 a. m.

## Redes Sociales Oficiales
- **Instagram:** https://www.instagram.com/magictourcolombia/
- **Facebook:** https://www.facebook.com/magictourcolombia/
- **YouTube:** https://www.youtube.com/@magictourcolombia
- **TikTok:** https://www.tiktok.com/@magictourcolombia
- **LinkedIn:** https://www.linkedin.com/company/magic-tour-colombia/
- **TripAdvisor:** Magic Tour Colombia (Certificado de Excelencia / Travelers' Choice)

- Fuente: https://magictourcolombia.com/es/contacto · Última revisión: {review_date}
"""


def _build_closures_and_seasons_markdown(review_date: str) -> str:
    return f"""# Calendario de Cierres de Parques y Temporadas Turísticas en Santa Marta

## 1. Cierres Temporales del Parque Nacional Natural Tayrona (`#RespiraTayrona`)
Por acuerdo entre Parques Nacionales Naturales de Colombia y los cuatro pueblos indígenas de la Sierra Nevada de Santa Marta (Kogui, Wiwa, Arhuaco y Kankuamo), el **Parque Nacional Natural Tayrona cierra temporalmente al ecoturismo tres (3) veces al año** para descanso espiritual, cultural y regeneración ecológica de los ecosistemas:

1. **Primer cierre anual:** **1 al 15 de febrero** (ambos días inclusive).
2. **Segundo cierre anual:** **1 al 15 de junio** (ambos días inclusive).
3. **Tercer cierre anual:** **19 de octubre al 2 de noviembre** (ambos días inclusive).

### Reglas operativas durante los cierres del Parque Tayrona
- Durante estas tres quincenas **no hay ingreso de visitantes** a ningún sector del Parque Tayrona (Cabo San Juan del Guía, Playa Cristal, Bahía Concha, Cañaveral, Arrecifes, Pueblito Taikú, Playa Cinto).
- **Alternativas recomendadas durante el cierre del Tayrona:**
  - **Minca y Sierra Nevada:** Tour a Minca (Café, Cacao y Cascadas Marinka), Cuatrimotos en Minca, Cerro Kennedy (2 días).
  - **Cultura y Arqueología en la Sierra Nevada:** Bunkuany (1, 2 o 3 días), Comunidad Indígena Kutunsama, Ciudad Perdida (excepto en su propio cierre de septiembre).
  - **Playas y Navegación fuera de la jurisdicción del PNN Tayrona:** Taganga y Playa Grande, Acuario y Playa Blanca en El Rodadero, Día en Velero a Playa Inca Inca, Atardecer en Velero por la Bahía de Santa Marta, Playa Calderón Beach Club, Pueblos Palafitos de la Ciénaga Grande y Aracataca (Macondo), o excursiones a Cabo de la Vela y Punta Gallinas (La Guajira).

---

## 2. Cierre Anual de Ciudad Perdida — Parque Arqueológico Teyuna (Calendario Oficial 2026)
Por disposición del Instituto Colombiano de Antropología e Historia (ICANH), Parques Nacionales Naturales y las autoridades indígenas de la Sierra Nevada:
- **Fechas oficiales de cierre en 2026:** **Del 1 al 31 de octubre de 2026** (anteriormente se realizaba en septiembre, pero para 2026 las comunidades indígenas y los gremios del sendero trasladaron el cierre al mes de octubre para pagamentos espirituales y regeneración del ecosistema).
- **Reanudación de salidas:** Ciudad Perdida reabre sus puertas el **1 de noviembre de 2026**; sin embargo, **los tours se reanudan desde el 30 de octubre**, dado que los primeros dos días del recorrido no ingresan a la zona arqueológica restringida.
- **Alternativas recomendadas durante el cierre de Ciudad Perdida en octubre:**
  - **Bunkuany (Terrazas Tayrona en la Sierra Nevada):** Disponible en modalidades de **1 día**, **2 días (Trek Arqueológico)** y **3 días (Comunidad Kogui)** — permite conocer terrazas arqueológicas Tayrona y compartir con comunidades indígenas.
  - **Minca y Sierra Nevada:** Tour a Minca (Café, Cacao y Cascada), Cuatrimotos en Minca, Cerro Kennedy (2 días).
  - **Parque Tayrona (únicamente del 1 al 18 de octubre):** Ten en cuenta que del **19 de octubre al 2 de noviembre** coincide con el tercer cierre de `#RespiraTayrona`.
  - **La Guajira:** Cabo de la Vela (2 días) y Punta Gallinas (3 días).

---

## 3. Temporadas Turísticas en Santa Marta y el Caribe Colombiano
- **Temporada Alta:**
  - **Fin de año y Reyes:** 15 de diciembre al 20 de enero.
  - **Semana Santa:** Domingo de Ramos a Domingo de Resurrección.
  - **Mitad de año:** 15 de junio al 20 de julio.
  - **Semana de Receso Escolar (octubre)** y **puentes festivos nacionales en Colombia**.
  - *Recomendación en temporada alta:* Reservar con mayor anticipación; los cupos diarios de ingreso al Parque Tayrona y a Ciudad Perdida son estrictamente limitados por capacidad de carga.
- **Temporada Baja / Media:** Resto del año (excelente disponibilidad y menor afluencia en senderos y playas).

- Fuente: https://magictourcolombia.com/es/cierre-ciudad-perdida y https://magictourcolombia.com/es/5-razones-parque-tayrona-cerrado · Última revisión: {review_date}
"""


async def main() -> int:
    configure_logging("INFO")
    log = get_logger()
    output_dir = PROJECT_ROOT / "tenants" / "magic-tour" / "knowledge"
    today_iso = date.today().isoformat()

    scraper = MagicTourScraper(review_date=today_iso)
    summary = await scraper.scrape_all(output_dir=output_dir)

    # Enrich contacto-y-canales.md with complete verified agency details
    contact_path = output_dir / "agencia" / "contacto-y-canales.md"
    existing_contact = (
        contact_path.read_text(encoding="utf-8") if contact_path.exists() else ""
    )
    enriched_contact = _build_official_contact_markdown(today_iso)
    if existing_contact and "## Contenido adicional del sitio" not in enriched_contact:
        contact_path.write_text(enriched_contact, encoding="utf-8")

    # Write verified closures and seasons guide
    closures_path = output_dir / "agencia" / "cierres_y_temporadas.md"
    closures_path.write_text(
        _build_closures_and_seasons_markdown(today_iso),
        encoding="utf-8",
    )
    if closures_path not in summary.written_files:
        summary.written_files.append(closures_path)
        summary.succeeded_count += 1

    # Verify with TenantKnowledgeLoader
    loader = TenantKnowledgeLoader(tenants_root=PROJECT_ROOT / "tenants")
    bundle = loader.load_tenant_knowledge("magic-tour")

    log.info(
        "scrape_cli_finished",
        succeededCount=summary.succeeded_count,
        failedCount=summary.failed_count,
        failedItems=summary.failed_items,
        loadedFiles=bundle.file_count,
        totalChars=bundle.total_chars,
        categories={k: len(v) for k, v in bundle.categories.items()},
    )
    return 0 if summary.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
