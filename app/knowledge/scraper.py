import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import structlog

from app.core.logging import external_call_duration_ms, get_logger
from app.knowledge.renderer import (
    html_to_markdown,
    render_page_markdown,
    render_tour_markdown,
)

DEFAULT_CMS_PAGES: dict[str, tuple[str, str]] = {
    "sobre-nosotros": ("agencia", "sobre-nosotros.md"),
    "contacto": ("agencia", "contacto-y-canales.md"),
    "trabaja-con-nosotros": ("agencia", "trabaja-con-nosotros.md"),
    "sostenibilidad": ("politicas", "sostenibilidad.md"),
    "comunicado-alerta-de-suplantacion": ("politicas", "alerta-de-suplantacion.md"),
    "terminos-y-condiciones": ("politicas", "terminos-y-condiciones.md"),
    "plan-de-cuotas-anticipadas": ("politicas", "plan-de-cuotas-anticipadas.md"),
    "politica-de-tratamiento-de-datos-personales": (
        "politicas",
        "politica-de-tratamiento-de-datos-personales.md",
    ),
    "preguntas-frecuentes": ("faq", "preguntas-frecuentes-agencia.md"),
    "tour-a-ciudad-perdida-8-preguntas-frecuentes": (
        "faq",
        "ciudad-perdida-8-preguntas-frecuentes.md",
    ),
    "diferencias-en-los-tours-de-ciudad-perdida-3-4-y-5-dias": (
        "faq",
        "diferencias-ciudad-perdida-3-4-y-5-dias.md",
    ),
}

DEFAULT_DESTINATION_SLUGS: list[str] = [
    "ciudad-perdida-colombia",
    "parque-nacional-natural-tayrona",
    "sierra-nevada-santa-marta",
    "santa-marta",
    "la-guajira",
    "alquiler-yates-lanchas-santa-marta",
    "cartagena",
    "barranquilla",
    "bahia-solano",
    "san-andres",
    "huila",
    "colombia",
]

DEFAULT_BLOG_SLUGS: list[str] = [
    "5-cosas-que-no-debes-olvidar-cuando-visites-el-parque-tayrona",
    "viajar-a-ciudad-perdida-con-ninos-es-posible",
    "minca-santa-marta-guia-completa",
    "aeropuerto-santa-marta",
    "terminal-transporte-santa-marta",
    "rodadero-santa-marta-guia-de-viaje",
    "como-viajar-seguro-por-colombia",
    "10-mejores-lugares-para-comer-y-beber-en-santa-marta",
    "que-debes-llevar-para-el-tour-a-ciudad-perdida",
    "la-guia-completa-sobre-la-ciudad-perdida-de-colombia",
    "por-que-elegir-a-magictour-colombia-para-hacer-el-tour-de-ciudad-perdida",
    "visitar-ciudad-perdida-en-cuantos-dias-puedo-hacerlo",
    "cierre-ciudad-perdida",
    "5-razones-parque-tayrona-cerrado",
    "como-llegar-y-que-hacer-en-el-parque-tayrona",
    "guia-practica-para-visitar-playa-cristal-parque-tayrona",
    "consejos-para-bahia-concha-parque-tayrona",
    "cabo-san-juan-naturaleza",
    "guia-definitiva-10-cosas-que-hacer-en-santa-marta",
    "playas-recomendadas-para-visitar-en-santa-marta",
    "por-que-reservar-transfers-privados-colombia",
    "santa-marta-destino-ideal-viajes-de-negocios-turismo-mice",
    "7-cosas-que-hacer-en-cabo-de-la-vela-guajira",
    "5-razones-para-visitar-punta-gallinas-la-guajira",
]


@dataclass
class ScrapeSummary:
    succeeded_count: int = 0
    failed_count: int = 0
    written_files: list[Path] = field(default_factory=list)
    failed_items: list[str] = field(default_factory=list)


def _slugify(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


def _render_blog_article_markdown(article: dict[str, Any], source_url: str, review_date: str) -> str:
    title = str(article.get("title") or "Guía de Viaje").strip()
    summary = str(article.get("shortDescription") or "").strip()
    blocks = article.get("content") or []

    lines: list[str] = [f"# {title}", ""]
    if summary:
        lines.extend([summary, ""])

    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "heading":
            level = min(6, max(2, int(block.get("level") or 2)))
            heading_text = html_to_markdown(str(block.get("content") or "")).strip()
            if heading_text:
                lines.extend(["", f"{'#' * level} {heading_text}", ""])
        elif btype in {"paragraph", "blockquote"}:
            raw = str(block.get("html") or block.get("content") or "")
            text_md = html_to_markdown(raw)
            if text_md:
                lines.extend([text_md, ""])
        elif btype in {"unordered-list", "ordered-list"}:
            items = block.get("html") or block.get("content") or []
            if isinstance(items, list):
                for item in items:
                    item_md = html_to_markdown(str(item)).strip()
                    if item_md:
                        lines.append(f"- {item_md}")
                lines.append("")

    lines.extend(["", f"- Fuente: {source_url} · Última revisión: {review_date}", ""])
    return "\n".join(lines)


class MagicTourScraper:
    def __init__(
        self,
        api_base_url: str = "https://api.magictourcolombia.com.co",
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        review_date: str | None = None,
        cms_pages: dict[str, tuple[str, str]] | None = None,
        destination_slugs: list[str] | None = None,
        blog_slugs: list[str] | None = None,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._review_date = review_date or date.today().isoformat()
        self._cms_pages = cms_pages if cms_pages is not None else DEFAULT_CMS_PAGES
        self._destination_slugs = (
            destination_slugs
            if destination_slugs is not None
            else DEFAULT_DESTINATION_SLUGS
        )
        self._blog_slugs = (
            blog_slugs if blog_slugs is not None else DEFAULT_BLOG_SLUGS
        )

    async def _fetch_json(self, client: httpx.AsyncClient, path: str) -> dict[str, Any]:
        url = f"{self._api_base_url}{path}"
        start = time.perf_counter()
        response = await client.get(
            url,
            headers={"Accept": "application/json", "Accept-Language": "es"},
        )
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        status_class = f"{response.status_code // 100}xx"
        external_call_duration_ms.observe(
            ("magictour_api", "get_json", status_class),
            duration_ms,
        )
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code} for {path}",
                request=response.request,
                response=response,
            )
        return dict(response.json())

    async def scrape_all(self, output_dir: Path) -> ScrapeSummary:
        run_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(
            requestId=run_id,
            entryPoint="cli_scraper",
        )
        log = get_logger()
        summary = ScrapeSummary()
        run_start = time.perf_counter()

        for subdir in ("tours", "agencia", "politicas", "tips", "faq"):
            (output_dir / subdir).mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            # 1. Productos / Tours / Servicios
            try:
                products_resp = await self._fetch_json(client, "/products?per_page=100")
                products_list = products_resp.get("data") or []
            except Exception as exc:
                log.error(
                    "scrape_item_failed",
                    category="products_index",
                    slug="all",
                    errorType=type(exc).__name__,
                )
                products_list = []
                summary.failed_count += 1
                summary.failed_items.append("products_index")

            for prod_summary in products_list:
                if not isinstance(prod_summary, dict):
                    continue
                raw_slug = prod_summary.get("slug")
                if not raw_slug:
                    title = str(prod_summary.get("title") or "").strip()
                    if not title:
                        continue
                    fallback_slug = f"servicio-{_slugify(title)}"
                    md = render_tour_markdown(prod_summary, review_date=self._review_date)
                    target = output_dir / "tours" / f"{fallback_slug}.md"
                    target.write_text(md, encoding="utf-8")
                    summary.succeeded_count += 1
                    summary.written_files.append(target)
                    log.info("scrape_item_succeeded", category="tours", slug=fallback_slug)
                    continue

                slug = str(raw_slug).strip()
                safe_filename = f"{_slugify(slug)}.md"
                try:
                    detail_resp = await self._fetch_json(
                        client, f"/products/detail/{slug}"
                    )
                    detail_data = detail_resp.get("data") or prod_summary
                    md = render_tour_markdown(detail_data, review_date=self._review_date)
                    target = output_dir / "tours" / safe_filename
                    target.write_text(md, encoding="utf-8")
                    summary.succeeded_count += 1
                    summary.written_files.append(target)
                    log.info("scrape_item_succeeded", category="tours", slug=slug)
                except Exception as exc:
                    summary.failed_count += 1
                    summary.failed_items.append(f"tour:{slug}")
                    log.error(
                        "scrape_item_failed",
                        category="tours",
                        slug=slug,
                        errorType=type(exc).__name__,
                    )

            # 2. Páginas CMS (Agencia, Políticas, FAQ)
            for slug, (subdir, filename) in self._cms_pages.items():
                try:
                    content_resp = await self._fetch_json(client, f"/es/content/{slug}")
                    data_obj = content_resp.get("data") or {}
                    source_url = f"https://magictourcolombia.com/es/{slug}"
                    if isinstance(data_obj.get("article"), dict):
                        md = _render_blog_article_markdown(
                            data_obj["article"],
                            source_url=source_url,
                            review_date=self._review_date,
                        )
                    else:
                        page_obj = data_obj.get("page") or data_obj
                        md = render_page_markdown(
                            page_obj,
                            source_url=source_url,
                            review_date=self._review_date,
                        )
                    target = output_dir / subdir / filename
                    target.write_text(md, encoding="utf-8")
                    summary.succeeded_count += 1
                    summary.written_files.append(target)
                    log.info("scrape_item_succeeded", category=subdir, slug=slug)
                except Exception as exc:
                    summary.failed_count += 1
                    summary.failed_items.append(f"page:{slug}")
                    log.error(
                        "scrape_item_failed",
                        category=subdir,
                        slug=slug,
                        errorType=type(exc).__name__,
                    )

            # 3. Destinos
            for dest_slug in self._destination_slugs:
                try:
                    dest_resp = await self._fetch_json(
                        client, f"/destinations/{dest_slug}"
                    )
                    dest_data = dest_resp.get("data") or {}
                    source_url = (
                        f"https://magictourcolombia.com/es/visit-colombia/{dest_slug}"
                    )
                    md = render_page_markdown(
                        dest_data,
                        source_url=source_url,
                        review_date=self._review_date,
                    )
                    target = output_dir / "tips" / f"destino-{_slugify(dest_slug)}.md"
                    target.write_text(md, encoding="utf-8")
                    summary.succeeded_count += 1
                    summary.written_files.append(target)
                    log.info(
                        "scrape_item_succeeded", category="destinations", slug=dest_slug
                    )
                except Exception as exc:
                    summary.failed_count += 1
                    summary.failed_items.append(f"destination:{dest_slug}")
                    log.error(
                        "scrape_item_failed",
                        category="destinations",
                        slug=dest_slug,
                        errorType=type(exc).__name__,
                    )

            # 4. Guías del Blog
            for blog_slug in self._blog_slugs:
                try:
                    blog_resp = await self._fetch_json(
                        client, f"/es/content/{blog_slug}"
                    )
                    data_obj = blog_resp.get("data") or {}
                    article_obj = data_obj.get("article") or {}
                    if not article_obj:
                        raise ValueError(f"Empty article for {blog_slug}")
                    source_url = f"https://magictourcolombia.com/es/{blog_slug}"
                    md = _render_blog_article_markdown(
                        article_obj,
                        source_url=source_url,
                        review_date=self._review_date,
                    )
                    target = output_dir / "tips" / f"guia-{_slugify(blog_slug)}.md"
                    target.write_text(md, encoding="utf-8")
                    summary.succeeded_count += 1
                    summary.written_files.append(target)
                    log.info("scrape_item_succeeded", category="blogs", slug=blog_slug)
                except Exception as exc:
                    summary.failed_count += 1
                    summary.failed_items.append(f"blog:{blog_slug}")
                    log.error(
                        "scrape_item_failed",
                        category="blogs",
                        slug=blog_slug,
                        errorType=type(exc).__name__,
                    )

        total_ms = round((time.perf_counter() - run_start) * 1000, 2)
        log.info(
            "scrape_run_completed",
            succeededCount=summary.succeeded_count,
            failedCount=summary.failed_count,
            durationMs=total_ms,
        )
        return summary
