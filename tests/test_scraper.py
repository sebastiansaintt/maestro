from pathlib import Path

import httpx
import pytest

from app.knowledge.scraper import MagicTourScraper


@pytest.mark.asyncio
async def test_scraper_writes_tours_agency_policies_tips_and_faq_files_and_handles_item_errors(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/products":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "9121",
                            "slug": "ciudad-perdida-santa-marta-4-dias",
                            "title": "Ciudad Perdida 4 Días",
                            "product_type": "tour",
                        },
                        {
                            "id": "9999",
                            "slug": "tour-roto-500",
                            "title": "Tour Roto",
                            "product_type": "tour",
                        },
                    ]
                },
            )
        if path == "/products/detail/ciudad-perdida-santa-marta-4-dias":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "9121",
                        "title": "Ciudad Perdida Santa Marta Tour 4 días",
                        "slug": "ciudad-perdida-santa-marta-4-dias",
                        "destination": "Ciudad Perdida Colombia",
                        "duration": "4 días",
                        "difficulty_level": "Difícil",
                        "price": {"full": 1860000, "currency": "COP", "book_with": 10},
                        "description": "<p>Trekking histórico a Teyuna.</p>",
                        "includes": ["Transporte 4x4", "Guía indígena"],
                        "excludes": ["Desayuno día 1"],
                    }
                },
            )
        if path == "/products/detail/tour-roto-500":
            return httpx.Response(500, json={"error": "Internal Error"})

        if path == "/es/content/sobre-nosotros":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "contentType": "page",
                        "page": {
                            "title": "Sobre Nosotros",
                            "content": "<p>Agencia con 20 años de experiencia y RNT 30835.</p>",
                        },
                    }
                },
            )
        if path == "/es/content/terminos-y-condiciones":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "contentType": "page",
                        "page": {
                            "title": "Términos y Condiciones",
                            "content": "<p>Política de cancelación y reembolsos.</p>",
                        },
                    }
                },
            )
        if path == "/es/content/preguntas-frecuentes":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "contentType": "page",
                        "page": {
                            "title": "Preguntas Frecuentes",
                            "content": "<p>¿Cómo reservar? Por WhatsApp o web.</p>",
                        },
                    }
                },
            )
        if path == "/destinations/parque-nacional-natural-tayrona":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "name": "Parque Nacional Natural Tayrona",
                        "description": "<p>Playas cristalinas y selva tropical.</p>",
                        "faqs": [
                            {
                                "question": "¿Qué llevar al Tayrona?",
                                "answer": "Documento de identidad y bloqueador.",
                            }
                        ],
                    }
                },
            )
        return httpx.Response(404, json={"error": "Not found"})

    scraper = MagicTourScraper(
        api_base_url="https://api.magictourcolombia.com.co",
        transport=httpx.MockTransport(handler),
        review_date="2026-10-19",
        cms_pages={
            "sobre-nosotros": ("agencia", "sobre-nosotros.md"),
            "terminos-y-condiciones": ("politicas", "terminos-y-condiciones.md"),
            "preguntas-frecuentes": ("faq", "preguntas-frecuentes.md"),
        },
        destination_slugs=["parque-nacional-natural-tayrona"],
        blog_slugs=[],
    )

    summary = await scraper.scrape_all(output_dir=tmp_path)

    assert summary.succeeded_count == 5
    assert summary.failed_count == 1

    tour_file = tmp_path / "tours" / "ciudad-perdida-santa-marta-4-dias.md"
    assert tour_file.exists()
    assert "1.860.000 COP" in tour_file.read_text(encoding="utf-8")

    about_file = tmp_path / "agencia" / "sobre-nosotros.md"
    assert about_file.exists()
    assert "RNT 30835" in about_file.read_text(encoding="utf-8")

    terms_file = tmp_path / "politicas" / "terminos-y-condiciones.md"
    assert terms_file.exists()

    faq_file = tmp_path / "faq" / "preguntas-frecuentes.md"
    assert faq_file.exists()

    dest_file = tmp_path / "tips" / "destino-parque-nacional-natural-tayrona.md"
    assert dest_file.exists()
    assert "Documento de identidad" in dest_file.read_text(encoding="utf-8")
