from app.knowledge.renderer import render_page_markdown, render_tour_markdown


def test_render_tour_markdown_produces_structured_prd_template_without_raw_html() -> None:
    sample_product = {
        "id": "9121",
        "title": "Ciudad Perdida Santa Marta Tour 4 días | Trekking Sierra Nevada",
        "slug": "ciudad-perdida-santa-marta-4-dias",
        "slug_es": "ciudad-perdida-santa-marta-4-dias",
        "slug_en": "lost-city-trek-colombia-4-days",
        "destination": "Ciudad Perdida Colombia",
        "product_type": "tour",
        "duration": "4 días y 3 noches",
        "difficulty_level": "Difícil",
        "minimum_people": 1,
        "minimum_age": 0,
        "groupSize": {"min": 1, "max": 15},
        "guide_languages": ["Español", "Inglés"],
        "meeting_point": "Hotel o lugar de alojamiento en el perímetro urbano de Santa Marta.",
        "schedule": [
            {"label": "Tour mañana", "departure_time": "07:00", "return_time": "17:00"}
        ],
        "price": {
            "full": 1860000,
            "currency": "COP",
            "book_with": 10,
            "withDiscount": 0,
        },
        "pricing": {
            "adults": 1860000,
            "children": 1860000,
            "foreignerAdults": 1860000,
            "currency": "COP",
            "book-with": 10,
            "has_foreign_pricing": False,
        },
        "description": "<p>Vive el <strong>trekking a Ciudad Perdida</strong>&nbsp;en 4 días.</p>",
        "itinerary": [
            {
                "day": 1,
                "title": "Santa Marta a Campamento Adán",
                "description": "Salida a las 7:00 a.m. hacia El Mamey y caminata inicial.",
            },
            {
                "day": 2,
                "title": "Campamento Adán a Campamento Paraíso",
                "description": "Caminata por territorio indígena Kogui.",
            },
        ],
        "includes": [
            "Transporte terrestre ida y regreso desde Santa Marta.",
            "Alimentación completa e hidratación.",
        ],
        "excludes": [
            "Desayuno del primer día.",
            "Servicio de mula para equipaje.",
        ],
        "recommendations": [
            "Botas de trekking",
            "Repelente y protector solar",
        ],
        "recommendations_text": "<p><strong>Preparación física:</strong> Caminata de 60 km en 4 días.</p>",
        "cancellation_policy": "<ul><li>Se considera No Show cuando no asisten.</li></ul>",
        "faqs": [
            {
                "question": "¿Cuál es la mejor época?",
                "answer": "De diciembre a marzo es temporada seca.",
            }
        ],
    }

    md = render_tour_markdown(sample_product, review_date="2026-10-19")

    assert md.startswith("# Ciudad Perdida Santa Marta Tour 4 días | Trekking Sierra Nevada\n")
    assert "1.860.000 COP" in md
    assert "Reserva con: 10%" in md
    assert "4 días y 3 noches" in md
    assert "Difícil" in md
    assert "07:00" in md and "17:00" in md
    assert "## Itinerario (día a día)" in md
    assert "### Día 1: Santa Marta a Campamento Adán" in md
    assert "## Incluye" in md
    assert "- Transporte terrestre ida y regreso desde Santa Marta." in md
    assert "## No incluye" in md
    assert "- Desayuno del primer día." in md
    assert "## Qué llevar" in md
    assert "- Botas de trekking" in md
    assert "## Recomendaciones / tips" in md
    assert "Preparación física: Caminata de 60 km en 4 días." in md
    assert "## Preguntas frecuentes" in md
    assert "**¿Cuál es la mejor época?**" in md
    assert "Fuente: https://magictourcolombia.com/es/tours/ciudad-perdida-santa-marta-4-dias" in md
    assert "Última revisión: 2026-10-19" in md
    assert "<p>" not in md
    assert "<strong>" not in md
    assert "<ul>" not in md
    assert "&nbsp;" not in md


def test_render_page_markdown_cleans_html_and_preserves_headings_and_lists() -> None:
    sample_page = {
        "title": "Sobre Nosotros: Conoce a Magic Tour Colombia",
        "slug": "sobre-nosotros",
        "content": "<h2>Nuestra Historia</h2><p>Más de 20 años en Santa Marta.</p><ul><li>RNT 30835</li><li>Sostenibilidad</li></ul>",
    }

    md = render_page_markdown(
        sample_page,
        source_url="https://magictourcolombia.com/es/sobre-nosotros",
        review_date="2026-10-19",
    )

    assert md.startswith("# Sobre Nosotros: Conoce a Magic Tour Colombia\n")
    assert "## Nuestra Historia" in md
    assert "Más de 20 años en Santa Marta." in md
    assert "- RNT 30835" in md
    assert "Fuente: https://magictourcolombia.com/es/sobre-nosotros · Última revisión: 2026-10-19" in md
    assert "<h2>" not in md
    assert "<p>" not in md
