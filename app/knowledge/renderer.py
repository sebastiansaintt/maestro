import html
import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag


def format_cop(amount: int | float | None) -> str:
    if amount is None:
        return "Consultar con asesor"
    val = int(round(float(amount)))
    if val == 0:
        return "Gratis / Basado en propinas (Free Tour)"
    formatted = f"{val:,}".replace(",", ".")
    return f"{formatted} COP"


def html_to_markdown(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    cleaned_raw = html.unescape(raw_html).replace("\xa0", " ")
    soup = BeautifulSoup(cleaned_raw, "html.parser")

    def render_node(node: Any) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""

        name = node.name.lower()
        inner = "".join(render_node(c) for c in node.children).strip()

        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = min(6, int(name[1]) if int(name[1]) >= 2 else 2)
            return f"\n\n{'#' * level} {inner}\n\n" if inner else ""
        if name == "p":
            return f"\n\n{inner}\n\n" if inner else ""
        if name == "br":
            return "\n"
        if name == "li":
            return f"- {inner}\n" if inner else ""
        if name in {"ul", "ol"}:
            items = "".join(render_node(c) for c in node.children)
            return f"\n{items}\n"
        if name in {"strong", "b", "em", "i", "span", "a", "div", "section", "article"}:
            return inner
        return inner

    result = "".join(render_node(c) for c in soup.contents)
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _extract_duration(duration_field: Any, duration_display: Any) -> str:
    if isinstance(duration_field, dict):
        es_val = str(duration_field.get("es") or "").strip()
        en_val = str(duration_field.get("en") or "").strip()
        if es_val and en_val and es_val != en_val:
            return f"{es_val} ({en_val})"
        if es_val:
            return es_val
    if isinstance(duration_field, str) and duration_field.strip():
        return duration_field.strip()
    if isinstance(duration_display, str) and duration_display.strip():
        return duration_display.strip()
    return "Consultar con asesor"


def render_tour_markdown(product: dict[str, Any], review_date: str) -> str:
    title = str(product.get("title") or product.get("slug") or "Tour").strip()
    slug = str(product.get("slug_es") or product.get("slug") or "").strip()
    slug_en = str(product.get("slug_en") or "").strip()
    destination = str(product.get("destination") or "Santa Marta y Caribe").strip()
    product_type = str(product.get("product_type") or "tour").strip()

    duration_str = _extract_duration(
        product.get("duration"), product.get("duration_display")
    )
    difficulty = str(product.get("difficulty_level") or "No especificado").strip()
    min_people = product.get("minimum_people") or 1
    min_age = product.get("minimum_age")
    group_size = product.get("groupSize") or {}
    group_max = group_size.get("max")

    guide_langs = product.get("guide_languages") or product.get("idiomas") or []
    langs_str = ", ".join(str(x) for x in guide_langs if x) or "Español"

    schedules = product.get("schedule") or []
    schedule_parts: list[str] = []
    for s in schedules:
        if isinstance(s, dict):
            dep = s.get("departure_time") or ""
            ret = s.get("return_time") or ""
            lbl = s.get("label") or ""
            if dep or ret:
                schedule_parts.append(f"{lbl}: salida {dep} - regreso {ret}".strip(": "))
    salidas_str = "; ".join(schedule_parts) if schedule_parts else "Salidas diarias (sujetas a disponibilidad)"

    price_obj = product.get("price") or {}
    pricing_obj = product.get("pricing") or {}
    full_price = (
        price_obj.get("full")
        if isinstance(price_obj, dict) and price_obj.get("full") is not None
        else product.get("priceToDisplay")
    )
    book_with = (
        price_obj.get("book_with")
        if isinstance(price_obj, dict) and price_obj.get("book_with") is not None
        else pricing_obj.get("book-with")
    )

    price_line = format_cop(full_price)
    if book_with:
        price_line += f" (Reserva con: {book_with}%)"

    extra_pricing_lines: list[str] = []
    if isinstance(pricing_obj, dict) and pricing_obj.get("adults") is not None:
        adults_p = pricing_obj.get("adults")
        children_p = pricing_obj.get("children")
        foreign_p = pricing_obj.get("foreignerAdults")
        if children_p is not None and children_p != adults_p:
            extra_pricing_lines.append(f"  - Niños: {format_cop(children_p)}")
        if pricing_obj.get("has_foreign_pricing") and foreign_p is not None and foreign_p != adults_p:
            extra_pricing_lines.append(f"  - Extranjeros (Adultos): {format_cop(foreign_p)}")

    lines: list[str] = [
        f"# {title}",
        f"- Destino: {destination} | Tipo: {product_type}",
        f"- Duración / Dificultad / Salidas: {duration_str} | Dificultad: {difficulty} | {salidas_str}",
        f"- Precio: {price_line}",
    ]
    lines.extend(extra_pricing_lines)
    lines.append(
        f"- Grupo y Requisitos: Mínimo {min_people} persona(s)"
        + (f", Máximo {group_max} personas" if group_max else "")
        + (f", Edad mínima: {min_age} años" if min_age is not None else "")
        + f" | Idiomas del guía: {langs_str}"
    )

    meeting_point = str(product.get("meeting_point") or "").strip()
    if meeting_point:
        lines.append(f"- Punto de encuentro: {html_to_markdown(meeting_point)}")

    if slug_en and slug_en != slug:
        lines.append(f"- Nombre / Slug en inglés: `{slug_en}`")

    description_md = html_to_markdown(product.get("description"))
    if description_md:
        lines.extend(["", "## Descripción", description_md])

    itinerary = product.get("itinerary") or []
    if itinerary:
        lines.extend(["", "## Itinerario (día a día)"])
        for item in itinerary:
            if isinstance(item, dict):
                day_num = item.get("day") or ""
                day_title = html_to_markdown(str(item.get("title") or "")).strip()
                day_desc = html_to_markdown(str(item.get("description") or "")).strip()
                lines.append(f"### Día {day_num}: {day_title}")
                if day_desc:
                    lines.append(day_desc)

    includes = [html_to_markdown(str(x)) for x in (product.get("includes") or []) if x]
    if includes:
        lines.extend(["", "## Incluye"])
        for inc in includes:
            lines.append(f"- {inc}")

    excludes = [html_to_markdown(str(x)) for x in (product.get("excludes") or []) if x]
    if excludes:
        lines.extend(["", "## No incluye"])
        for exc in excludes:
            lines.append(f"- {exc}")

    what_to_bring = [
        html_to_markdown(str(x)) for x in (product.get("recommendations") or []) if x
    ]
    if what_to_bring:
        lines.extend(["", "## Qué llevar"])
        for item_str in what_to_bring:
            lines.append(f"- {item_str}")

    rec_text_md = html_to_markdown(product.get("recommendations_text"))
    if rec_text_md:
        lines.extend(["", "## Recomendaciones / tips", rec_text_md])

    cancel_md = html_to_markdown(product.get("cancellation_policy"))
    if cancel_md:
        lines.extend(["", "## Políticas de cancelación", cancel_md])

    faqs = product.get("faqs") or []
    if faqs:
        lines.extend(["", "## Preguntas frecuentes"])
        for faq in faqs:
            if isinstance(faq, dict):
                q = html_to_markdown(str(faq.get("question") or "")).strip()
                a = html_to_markdown(str(faq.get("answer") or "")).strip()
                if q and a:
                    lines.append(f"- **{q}**")
                    lines.append(f"  {a}")

    source_url = (
        f"https://magictourcolombia.com/es/tours/{slug}"
        if slug
        else "https://magictourcolombia.com/es/tours"
    )
    lines.extend(["", f"- Fuente: {source_url} · Última revisión: {review_date}", ""])
    return "\n".join(lines)


def render_page_markdown(
    page: dict[str, Any], source_url: str, review_date: str
) -> str:
    title = str(page.get("title") or page.get("name") or "Información").strip()
    content_html = str(page.get("content") or page.get("description") or "")
    body_md = html_to_markdown(content_html)

    lines: list[str] = [f"# {title}", ""]
    if body_md:
        lines.append(body_md)

    faqs = page.get("faqs") or []
    if faqs:
        lines.extend(["", "## Preguntas frecuentes"])
        for faq in faqs:
            if isinstance(faq, dict):
                q = html_to_markdown(str(faq.get("question") or "")).strip()
                a = html_to_markdown(str(faq.get("answer") or "")).strip()
                if q and a:
                    lines.append(f"- **{q}**")
                    lines.append(f"  {a}")

    lines.extend(["", f"- Fuente: {source_url} · Última revisión: {review_date}", ""])
    return "\n".join(lines)
