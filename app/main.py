from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import secrets
import time
import uuid
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.conversation.debouncer import MessageDebouncer
from app.conversation.lifecycle import SessionLifecycleManager
from app.conversation.orchestrator import (
    AUDIO_REJECTED_SENTINEL,
    PAUSED_BY_HANDOFF_SENTINEL,
    RECEIPT_HANDOFF_SENTINEL,
    ConversationOrchestrator,
)
from app.core.config import settings
from app.core.logging import (
    configure_logging,
    get_logger,
    http_request_duration_ms,
)
from app.db.repository import SessionRepository
from app.llm.base import LLMProvider
from app.llm.gemini import GeminiLLMProvider
from app.metrics.panel import render_panel_html
from app.metrics.service import MetricsService, render_markdown_report
from app.providers.whatsapp.base import (
    SentMessageRegistry,
    TrackingWhatsAppProvider,
    WhatsAppProvider,
)
from app.providers.whatsapp.evolution import EvolutionWhatsAppProvider


def _extract_auth_token(request: Request) -> str:
    header_tok = request.headers.get("x-report-token")
    if header_tok:
        return header_tok.strip()
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    query_tok = request.query_params.get("token")
    if query_tok:
        return query_tok.strip()
    return ""


def create_app(
    whatsapp_provider: WhatsAppProvider | None = None,
    llm_provider: LLMProvider | None = None,
    tenants_root: Path = Path("tenants"),
    repository: SessionRepository | None = None,
    debounce_delay_seconds: float = 0.0,
    run_background_jobs: bool = False,
    report_api_token: str | None = None,
    allow_default_llm: bool = True,
) -> FastAPI:
    configure_logging(settings.log_level)

    raw_wa = whatsapp_provider or EvolutionWhatsAppProvider(
        base_url=settings.evolution_api_url,
        api_key=settings.evolution_api_key,
    )
    tracked_wa = TrackingWhatsAppProvider(raw_wa, SentMessageRegistry())

    resolved_llm = llm_provider
    if resolved_llm is None and allow_default_llm and settings.gemini_api_key:
        fallback_list = [
            m.strip()
            for m in settings.gemini_fallback_models.split(",")
            if m.strip()
        ]
        resolved_llm = GeminiLLMProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            fallback_models=fallback_list,
            reasoning_effort=settings.gemini_reasoning_effort,
        )

    orchestrator = ConversationOrchestrator(
        whatsapp_provider=tracked_wa,
        llm_provider=resolved_llm,
        tenants_root=tenants_root,
        repository=repository,
    )
    debouncer = MessageDebouncer(
        handler=orchestrator.handle_text_message,
        delay_seconds=debounce_delay_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        checker: asyncio.Task[None] | None = None
        if run_background_jobs and repository is not None:
            await repository.init_db()
            lifecycle = SessionLifecycleManager(repository=repository, whatsapp_provider=tracked_wa)
            checker = asyncio.create_task(lifecycle.run_periodic_checker(interval_seconds=60.0))
        yield
        if checker is not None:
            checker.cancel()
        await debouncer.shutdown()

    app = FastAPI(title="Maestro - WhatsApp AI Assistant", version="0.6.0", lifespan=lifespan)
    app.state.whatsapp_provider = tracked_wa
    app.state.llm_provider = resolved_llm
    app.state.orchestrator = orchestrator
    app.state.debouncer = debouncer
    app.state.debounce_delay_seconds = debounce_delay_seconds
    app.state.repository = repository
    app.state.seen_message_ids = SentMessageRegistry(max_size=4096)
    app.state.report_api_token = (
        report_api_token if report_api_token is not None else settings.report_api_token
    )

    def _verify_report_token(request: Request) -> None:
        provided = _extract_auth_token(request)
        expected = str(request.app.state.report_api_token or "")
        if not provided or not expected or not secrets.compare_digest(provided, expected):
            get_logger().warning("report_access_denied", path=request.url.path)
            raise HTTPException(status_code=401, detail="Unauthorized report access")

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        entry_point = (
            "webhook_evolution"
            if request.url.path == "/webhook/evolution"
            else "http_api"
        )
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            requestId=request_id,
            entryPoint=entry_point,
        )
        request.state.request_id = request_id

        start = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        status_class = f"{response.status_code // 100}xx"
        http_request_duration_ms.observe(
            (request.method, request.url.path, status_class),
            duration_ms,
        )
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/reporte")
    async def get_reporte(
        request: Request,
        tenant_id: str = settings.tenant_default_id,
        format: str = "json",
    ) -> Response:
        _verify_report_token(request)
        repo: SessionRepository = request.app.state.repository or SessionRepository(
            db_path=settings.db_path
        )
        metrics_svc = MetricsService(repository=repo)
        report = await metrics_svc.build_tenant_report(tenant_id=tenant_id)
        if format.lower() in {"markdown", "md"}:
            return PlainTextResponse(
                content=render_markdown_report(report),
                media_type="text/markdown; charset=utf-8",
            )
        from fastapi.responses import JSONResponse

        return JSONResponse(content=report.to_dict())

    @app.get("/panel", response_class=HTMLResponse)
    async def get_panel(
        request: Request,
        tenant_id: str = settings.tenant_default_id,
    ) -> HTMLResponse:
        _verify_report_token(request)
        repo: SessionRepository = request.app.state.repository or SessionRepository(
            db_path=settings.db_path
        )
        metrics_svc = MetricsService(repository=repo)
        report = await metrics_svc.build_tenant_report(tenant_id=tenant_id)
        return HTMLResponse(content=render_panel_html(report))

    @app.post("/webhook/evolution")
    async def webhook_evolution(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        log = get_logger()
        instance = str(payload.get("instance") or settings.evolution_default_instance)
        event_type = str(payload.get("event") or "unknown")
        tenant_id = instance

        log.info(
            "webhook_received",
            tenantId=tenant_id,
            instance=instance,
            eventType=event_type,
        )

        if event_type != "messages.upsert":
            log.info("webhook_ignored", tenantId=tenant_id, reason="unsupported_event")
            return {"status": "ignored", "reason": "unsupported_event"}

        data = payload.get("data") or {}
        key = data.get("key") or {}
        remote_jid = str(key.get("remoteJid") or "")

        if remote_jid.endswith("@g.us"):
            log.info("webhook_ignored", tenantId=tenant_id, reason="group_message")
            return {"status": "ignored", "reason": "group_message"}

        recipient_number = remote_jid.split("@")[0]
        message_id = str(key.get("id") or "")

        message_obj = data.get("message") or {}
        extended_obj = message_obj.get("extendedTextMessage") or {}
        text = str(
            message_obj.get("conversation")
            or extended_obj.get("text")
            or ""
        ).strip()

        if key.get("fromMe") is True:
            if message_id and message_id in request.app.state.seen_message_ids:
                log.info("webhook_ignored", tenantId=tenant_id, reason="duplicate_message", messageId=message_id)
                return {"status": "ignored", "reason": "duplicate_message"}
            result = await request.app.state.orchestrator.handle_from_me_message(
                tenant_id=tenant_id,
                sender_number=recipient_number,
                text=text,
                message_id=message_id,
            )
            if message_id and result.get("reason") != "bot_echo":
                request.app.state.seen_message_ids.add(message_id)
            if result.get("status") == "ignored":
                log.info("webhook_ignored", tenantId=tenant_id, reason=result.get("reason"))
            return result

        if message_id:
            if message_id in request.app.state.seen_message_ids:
                log.info("webhook_ignored", tenantId=tenant_id, reason="duplicate_message", messageId=message_id)
                return {"status": "ignored", "reason": "duplicate_message"}
            request.app.state.seen_message_ids.add(message_id)

        audio_obj = message_obj.get("audioMessage")
        if recipient_number and isinstance(audio_obj, dict):
            seconds = int(audio_obj.get("seconds") or 0)
            mimetype = str(audio_obj.get("mimetype") or "audio/ogg")
            inline_b64 = audio_obj.get("base64") or message_obj.get("base64")
            audio_res = await request.app.state.orchestrator.handle_audio_message(
                tenant_id=tenant_id,
                instance=instance,
                sender_number=recipient_number,
                message_id=message_id,
                seconds=seconds,
                mimetype=mimetype,
                request_id=request.state.request_id,
                inline_base64=str(inline_b64) if inline_b64 else None,
            )
            if audio_res == AUDIO_REJECTED_SENTINEL:
                return {"status": "audio_rejected", "tenant_id": tenant_id}
            if audio_res == PAUSED_BY_HANDOFF_SENTINEL:
                return {"status": "paused_by_handoff", "tenant_id": tenant_id}
            return {"status": "processed", "tenant_id": tenant_id}

        image_obj = message_obj.get("imageMessage")
        doc_obj = (
            message_obj.get("documentMessage")
            or (message_obj.get("documentWithCaptionMessage") or {})
            .get("message", {})
            .get("documentMessage")
        )
        if recipient_number and (isinstance(image_obj, dict) or isinstance(doc_obj, dict)):
            media_dict = image_obj if isinstance(image_obj, dict) else (doc_obj or {})
            media_kind = "image" if isinstance(image_obj, dict) else "document"
            caption = str(media_dict.get("caption") or "").strip()
            filename = str(media_dict.get("fileName") or "").strip()
            media_res = await request.app.state.orchestrator.handle_media_message(
                tenant_id=tenant_id,
                instance=instance,
                sender_number=recipient_number,
                media_kind=media_kind,
                caption=caption,
                filename=filename,
                request_id=request.state.request_id,
            )
            if media_res == RECEIPT_HANDOFF_SENTINEL:
                return {"status": "receipt_handoff", "tenant_id": tenant_id}
            if media_res == PAUSED_BY_HANDOFF_SENTINEL:
                return {"status": "paused_by_handoff", "tenant_id": tenant_id}
            return {"status": "processed", "tenant_id": tenant_id}

        if recipient_number and text:
            if request.app.state.debounce_delay_seconds > 0.0:
                await request.app.state.debouncer.enqueue(
                    tenant_id=tenant_id,
                    instance=instance,
                    sender_number=recipient_number,
                    text=text,
                    request_id=request.state.request_id,
                )
            else:
                reply = await request.app.state.orchestrator.handle_text_message(
                    tenant_id=tenant_id,
                    instance=instance,
                    sender_number=recipient_number,
                    text=text,
                    request_id=request.state.request_id,
                )
                if reply == PAUSED_BY_HANDOFF_SENTINEL:
                    return {"status": "paused_by_handoff", "tenant_id": tenant_id}

        return {"status": "processed", "tenant_id": tenant_id}

    return app


app = create_app(
    repository=SessionRepository(db_path=settings.db_path),
    debounce_delay_seconds=settings.debounce_seconds,
    run_background_jobs=True,
)
