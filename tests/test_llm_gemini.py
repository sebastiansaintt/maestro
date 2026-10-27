import json
import httpx
import pytest

from app.llm.base import LLMProviderError, LLMRequest
from app.llm.gemini import GeminiLLMProvider
from app.llm.prompts import TenantPromptConfig, build_system_prompt


def test_build_system_prompt_includes_ley_1581_language_rules_and_knowledge_block() -> None:
    config = TenantPromptConfig(
        tenant_id="magic-tour",
        agency_name="Magic Tour Colombia",
        privacy_policy_url="https://magictourcolombia.com/es/politica-de-tratamiento-de-datos-personales",
        supported_languages=("es", "en"),
    )
    prompt = build_system_prompt(
        tenant_config=config,
        knowledge_markdown="# Ciudad Perdida 4 días\n- Precio: 1.860.000 COP",
        is_first_contact=True,
    )

    # Must include agency identity, Ley 1581 notice & URL, ES/EN restriction, no-quota rule, and knowledge
    assert "Magic Tour Colombia" in prompt
    assert "https://magictourcolombia.com/es/politica-de-tratamiento-de-datos-personales" in prompt
    assert "Ley 1581" in prompt
    assert "Español" in prompt and "Inglés" in prompt
    assert "un asesor te confirma el cupo exacto" in prompt
    assert "<MENSAJE_USUARIO>" in prompt
    assert "<BASE_DE_CONOCIMIENTO>" in prompt
    assert "1.860.000 COP" in prompt


@pytest.mark.asyncio
async def test_gemini_provider_sends_low_thinking_config_and_returns_tokens_and_cost() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        "¡Hola! 🌴 Soy el asistente de reservas de Magic Tour Colombia. "
                                        "El tour a Ciudad Perdida de 4 días cuesta 1.860.000 COP."
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 2000,
                    "candidatesTokenCount": 50,
                    "totalTokenCount": 2050,
                },
            },
        )

    provider = GeminiLLMProvider(
        api_key="secret_gemini_key_do_not_leak",
        model="gemini-3.8-flash",
        reasoning_effort="low",
        transport=httpx.MockTransport(handler),
    )

    response = await provider.generate_reply(
        LLMRequest(
            tenant_id="magic-tour",
            system_prompt="Directivas del sistema...",
            user_message="<MENSAJE_USUARIO>\n¿Precio de Ciudad Perdida?\n</MENSAJE_USUARIO>",
        )
    )

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.url.path.endswith("/models/gemini-3.8-flash:generateContent")
    # API key must be in header, never in URL query params
    assert "secret_gemini_key_do_not_leak" not in str(req.url)
    assert req.headers.get("x-goog-api-key") == "secret_gemini_key_do_not_leak"

    body = json.loads(req.content.decode("utf-8"))
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
    assert "Directivas del sistema..." in body["systemInstruction"]["parts"][0]["text"]

    assert "1.860.000 COP" in response.text
    assert response.tokens_in == 2000
    assert response.tokens_out == 50
    assert response.cost_usd > 0


@pytest.mark.asyncio
async def test_gemini_provider_raises_domain_error_without_leaking_api_key_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "Service Unavailable"}})

    provider = GeminiLLMProvider(
        api_key="super_secret_gemini_api_key_999",
        model="gemini-3.8-flash",
        fallback_models=(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.generate_reply(
            LLMRequest(
                tenant_id="magic-tour",
                system_prompt="System",
                user_message="<MENSAJE_USUARIO>Hola</MENSAJE_USUARIO>",
            )
        )

    assert "super_secret_gemini_api_key_999" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_waterfall_retries_fallback_models_and_omits_thinking_config_for_3_5_3_6() -> None:
    attempted_models: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model_name = request.url.path.split("/models/")[1].split(":")[0]
        body = json.loads(request.content.decode("utf-8"))
        attempted_models.append((model_name, body))

        if model_name == "gemini-3-flash-preview":
            return httpx.Response(503, json={"error": {"message": "Overloaded"}})
        if model_name == "gemini-3.5-flash":
            raise httpx.ReadTimeout("Timed out")
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": '{"reply":"Respuesta desde fallback"}'}]}}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
            },
        )

    provider = GeminiLLMProvider(
        api_key="secret_key",
        model="gemini-3-flash-preview",
        fallback_models=("gemini-3.5-flash", "gemini-3.1-flash-lite"),
        reasoning_effort="low",
        transport=httpx.MockTransport(handler),
    )

    response = await provider.generate_reply(
        LLMRequest(
            tenant_id="magic-tour",
            system_prompt="System",
            user_message="<MENSAJE_USUARIO>Hola</MENSAJE_USUARIO>",
        )
    )

    assert [m for m, _ in attempted_models] == [
        "gemini-3-flash-preview",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]
    # 3.5 must omit thinkingConfig so Google AI Studio does not hang
    assert "thinkingConfig" in attempted_models[0][1]["generationConfig"]
    assert "thinkingConfig" not in attempted_models[1][1]["generationConfig"]
    assert "thinkingConfig" in attempted_models[2][1]["generationConfig"]
    assert response.model == "gemini-3.1-flash-lite"
    assert "Respuesta desde fallback" in response.text
