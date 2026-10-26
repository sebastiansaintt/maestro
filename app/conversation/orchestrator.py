from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from app.conversation.handoff import HandoffService
from app.conversation.office_hours import OfficeHoursPolicy
from app.conversation.payments import (
    build_payment_gate_message,
    evaluate_payment_gate,
    format_payment_message,
    load_payment_config,
)
from app.core.logging import get_logger
from app.db.models import SessionRecord
from app.db.repository import SessionRepository
from app.knowledge.catalog import TourCatalogEntry, load_tour_catalog
from app.knowledge.loader import TenantKnowledgeBundle, TenantKnowledgeLoader
from app.llm.base import ChatMessage, LLMProvider, LLMProviderError, LLMRequest, TranscriptionRequest
from app.llm.prompts import (
    TenantPromptConfig,
    build_system_prompt,
    load_tenant_prompt_config,
)
from app.providers.whatsapp.base import (
    MediaPayload,
    OutboundTextMessage,
    SentMessageRegistry,
    TrackingWhatsAppProvider,
    WhatsAppProvider,
    WhatsAppProviderError,
)
from app.security.guardrails import MAX_AUDIO_SECONDS, sanitize_user_input, validate_bot_output

logger = get_logger()

PAUSED_BY_HANDOFF_SENTINEL = "__PAUSED_BY_HANDOFF__"
AUDIO_REJECTED_SENTINEL = "__AUDIO_REJECTED__"
RECEIPT_HANDOFF_SENTINEL = "__RECEIPT_HANDOFF__"

_RECEIPT_KEYWORDS: tuple[str, ...] = (
    "comprobante",
    "pago",
    "transferencia",
    "recibo",
    "consignacion",
    "consignación",
    "nequi",
    "bancolombia",
    "receipt",
    "payment",
    "transfer",
)

_AUDIO_NOTICES = {
    "too_long": {
        "es": "Tu nota de voz supera los 90 segundos 🙏. ¿Me la envías más corta o me escribes tu consulta?",
        "en": "Your voice note is longer than 90 seconds 🙏. Could you send a shorter one or type your question?",
    },
    "failed": {
        "es": "No pude escuchar bien tu nota de voz 🙏. ¿Me escribes tu consulta, por favor?",
        "en": "I couldn't process your voice note 🙏. Could you please type your question?",
    },
}


def _audio_notice(kind: str, language: str) -> str:
    texts = _AUDIO_NOTICES[kind]
    if language in texts:
        return texts[language]
    return f"{texts['es']}\n\n{texts['en']}"  # idioma aún desconocido: ES + EN


@dataclass(frozen=True)
class ParsedLLMTurn:
    reply: str
    intent: str
    language: str
    lead: dict[str, Any]


def _parse_llm_output(raw_text: str) -> ParsedLLMTurn:
    stripped = (raw_text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()

    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and "reply" in obj:
                lead_raw = obj.get("lead")
                return ParsedLLMTurn(
                    reply=str(obj.get("reply") or "").strip(),
                    intent=str(obj.get("intent") or "none").strip().lower(),
                    language="en" if str(obj.get("language") or "es").lower() == "en" else "es",
                    lead=dict(lead_raw) if isinstance(lead_raw, dict) else {},
                )
        except json.JSONDecodeError:
            pass

    return ParsedLLMTurn(
        reply=stripped,
        intent="none",
        language="es",
        lead={},
    )


def _merge_lead(existing: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for k, v in updates.items():
        if v is not None and str(v).strip() != "":
            merged[k] = v
    return merged


class ConversationOrchestrator:
    """Coordinates Knowledge, InputGuard, Gemini LLM, OutputGuard, Payment Gate, Handoff, and SQLite."""

    def __init__(
        self,
        whatsapp_provider: WhatsAppProvider,
        llm_provider: LLMProvider | None = None,
        tenants_root: Path = Path("tenants"),
        repository: SessionRepository | None = None,
        office_hours: OfficeHoursPolicy | None = None,
    ) -> None:
        self._whatsapp_provider: TrackingWhatsAppProvider = (
            whatsapp_provider
            if isinstance(whatsapp_provider, TrackingWhatsAppProvider)
            else TrackingWhatsAppProvider(whatsapp_provider, SentMessageRegistry())
        )
        self._sent_registry = self._whatsapp_provider.registry
        self._llm_provider = llm_provider
        self._tenants_root = tenants_root
        self._repository = repository
        self._office_hours = office_hours or OfficeHoursPolicy()
        self._knowledge_loader = TenantKnowledgeLoader(tenants_root=tenants_root)
        self._knowledge_cache: dict[str, TenantKnowledgeBundle] = {}
        self._config_cache: dict[str, TenantPromptConfig] = {}
        self._catalog_cache: dict[str, dict[str, TourCatalogEntry]] = {}
        self._in_memory_history: dict[tuple[str, str], list[ChatMessage]] = {}
        self._in_memory_leads: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def whatsapp_provider(self) -> TrackingWhatsAppProvider:
        return self._whatsapp_provider

    def _get_tenant_context(
        self, tenant_id: str
    ) -> tuple[TenantPromptConfig, TenantKnowledgeBundle, dict[str, TourCatalogEntry]]:
        if tenant_id not in self._config_cache:
            self._config_cache[tenant_id] = load_tenant_prompt_config(
                tenant_id=tenant_id,
                tenants_root=self._tenants_root,
            )
        if tenant_id not in self._knowledge_cache:
            self._knowledge_cache[tenant_id] = (
                self._knowledge_loader.load_tenant_knowledge(tenant_id)
            )
        if tenant_id not in self._catalog_cache:
            self._catalog_cache[tenant_id] = load_tour_catalog(
                tenant_id=tenant_id,
                tenants_root=self._tenants_root,
            )
        return (
            self._config_cache[tenant_id],
            self._knowledge_cache[tenant_id],
            self._catalog_cache[tenant_id],
        )

    async def _send_whatsapp(
        self,
        *,
        instance: str,
        recipient_number: str,
        text: str,
        request_id: str,
    ) -> None:
        await self._whatsapp_provider.send_text(
            OutboundTextMessage(
                instance=instance,
                recipient_number=recipient_number,
                text=text,
                request_id=request_id,
            )
        )

    async def handle_from_me_message(
        self,
        *,
        tenant_id: str,
        sender_number: str,
        text: str,
        message_id: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Mensajes salientes del número del negocio: eco del bot, comandos o escritura manual del asesor."""
        if message_id and message_id in self._sent_registry:
            return {"status": "ignored", "reason": "bot_echo"}
        if self._repository is None or not sender_number:
            return {"status": "ignored", "reason": "from_me"}

        clean = (text or "").strip().lower()
        now_dt = now or datetime.now(timezone.utc)
        session, _ = await self._repository.get_or_create_active_session(
            tenant_id=tenant_id,
            phone_number=sender_number,
            now=now_dt,
        )

        if clean == "#activar":
            await self._repository.update_session(session.id, status="ACTIVA", clear_pause=True)
            logger.info("advisor_command_executed", tenantId=tenant_id, sessionId=session.id, command="activar")
            return {"status": "advisor_command", "command": "activar"}

        # "#pausar" o cualquier mensaje manual del asesor: el bot se silencia 24 h (RF-13)
        await self._repository.update_session(
            session.id,
            status="ESPERANDO_ASESOR",
            paused_until=now_dt + timedelta(hours=24),
        )
        if clean == "#pausar":
            logger.info("advisor_command_executed", tenantId=tenant_id, sessionId=session.id, command="pausar")
            return {"status": "advisor_command", "command": "pausar"}

        if text:
            await self._repository.append_message(
                session_id=session.id,
                role="advisor",
                msg_type="text",
                content=text,
                timestamp=now_dt,
            )
        logger.info("advisor_manual_message_pause_reset", tenantId=tenant_id, sessionId=session.id)
        return {"status": "advisor_message", "tenant_id": tenant_id}

    async def handle_audio_message(
        self,
        *,
        tenant_id: str,
        instance: str,
        sender_number: str,
        message_id: str,
        seconds: int,
        mimetype: str,
        request_id: str,
        inline_base64: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Notas de voz (RF-19): límite de 90 s, transcripción con Gemini y mismo flujo blindado que el texto."""
        now_dt = now or datetime.now(timezone.utc)

        session: SessionRecord | None = None
        if self._repository is not None:
            session, _ = await self._repository.get_or_create_active_session(
                tenant_id=tenant_id, phone_number=sender_number, now=now_dt
            )
            if session.paused_until is not None and session.paused_until > now_dt:
                await self._repository.append_message(
                    session_id=session.id, role="user", msg_type="audio",
                    content=f"[nota de voz de {seconds} s]", timestamp=now_dt,
                )
                return PAUSED_BY_HANDOFF_SENTINEL

        language = str((session.reservation_data if session else {}).get("language") or "")

        if seconds > MAX_AUDIO_SECONDS:
            logger.warning("audio_rejected_too_long", tenantId=tenant_id, seconds=seconds)
            if self._repository is not None and session is not None:
                await self._repository.append_message(
                    session_id=session.id, role="user", msg_type="audio",
                    content=f"[nota de voz rechazada: {seconds} s]", timestamp=now_dt,
                )
            await self._send_whatsapp(
                instance=instance, recipient_number=sender_number,
                text=_audio_notice("too_long", language), request_id=request_id,
            )
            return AUDIO_REJECTED_SENTINEL

        if self._llm_provider is None:
            return AUDIO_REJECTED_SENTINEL

        try:
            if inline_base64:
                media = MediaPayload(base64_data=inline_base64, mimetype=mimetype.split(";")[0].strip())
            else:
                media = await self._whatsapp_provider.get_media_base64(instance, message_id)
            transcription = await self._llm_provider.transcribe_audio(
                TranscriptionRequest(tenant_id=tenant_id, base64_data=media.base64_data, mimetype=media.mimetype)
            )
        except (WhatsAppProviderError, LLMProviderError, NotImplementedError) as exc:
            logger.error("audio_transcription_failed", tenantId=tenant_id, errorType=type(exc).__name__)
            await self._send_whatsapp(
                instance=instance, recipient_number=sender_number,
                text=_audio_notice("failed", language), request_id=request_id,
            )
            return AUDIO_REJECTED_SENTINEL

        transcript = transcription.text.strip()
        if self._repository is not None and session is not None:
            await self._repository.record_event(
                tenant_id=tenant_id,
                session_id=session.id,
                event_type="TOKEN_USAGE",
                payload={"model": transcription.model, "kind": "transcription", "seconds": seconds},
                tokens_in=transcription.tokens_in,
                tokens_out=transcription.tokens_out,
                cost_usd=transcription.cost_usd,
                is_off_hours=self._office_hours.is_off_hours(now_dt),
                timestamp=now_dt,
            )
        logger.info("audio_transcribed", tenantId=tenant_id, seconds=seconds, transcriptChars=len(transcript))

        return await self.handle_text_message(
            tenant_id=tenant_id,
            instance=instance,
            sender_number=sender_number,
            text=transcript,
            request_id=request_id,
            now=now_dt,
            msg_type="audio",
            audio_transcription=transcript,
        )

    async def handle_media_message(
        self,
        *,
        tenant_id: str,
        instance: str,
        sender_number: str,
        media_kind: str,  # "image" | "document"
        caption: str = "",
        filename: str = "",
        request_id: str = "",
        now: datetime | None = None,
    ) -> str:
        """Recepción de imágenes y documentos (RF-10 comprobantes / RF-20 otras imágenes)."""
        now_dt = now or datetime.now(timezone.utc)
        is_off_hours = self._office_hours.is_off_hours(now_dt)

        session: SessionRecord | None = None
        if self._repository is not None:
            session, _ = await self._repository.get_or_create_active_session(
                tenant_id=tenant_id,
                phone_number=sender_number,
                now=now_dt,
            )
            if session.paused_until is not None and session.paused_until > now_dt:
                await self._repository.append_message(
                    session_id=session.id,
                    role="user",
                    msg_type="image",
                    content=caption or f"[{media_kind} recibido]",
                    timestamp=now_dt,
                )
                return PAUSED_BY_HANDOFF_SENTINEL

        combined_hint = f"{caption} {filename}".lower()
        has_receipt_keyword = any(kw in combined_hint for kw in _RECEIPT_KEYWORDS)
        is_awaiting_payment = session is not None and session.status == "ESPERANDO_PAGO"
        is_receipt = is_awaiting_payment or media_kind == "document" or has_receipt_keyword

        if is_receipt and self._repository is not None and session is not None:
            tenant_config, _, _ = self._get_tenant_context(tenant_id)
            language = str((session.reservation_data or {}).get("language") or "es")
            await self._repository.append_message(
                session_id=session.id,
                role="user",
                msg_type="image",
                content=caption or f"[comprobante {media_kind}]",
                timestamp=now_dt,
            )
            await self._repository.record_event(
                tenant_id=tenant_id,
                session_id=session.id,
                event_type="PAYMENT_RECEIPT",
                payload={"media_kind": media_kind, "caption": caption, "filename": filename},
                is_off_hours=is_off_hours,
                timestamp=now_dt,
            )
            handoff_svc = HandoffService(
                repository=self._repository,
                whatsapp_provider=self._whatsapp_provider,
                office_hours=self._office_hours,
            )
            handoff_res = await handoff_svc.trigger(
                tenant_id=tenant_id,
                instance=instance,
                advisor_group_jid=tenant_config.advisor_group_jid,
                session=session,
                phone_number=sender_number,
                reason="payment_receipt",
                language=language,
                request_id=request_id,
                now=now_dt,
            )
            await self._repository.append_message(
                session_id=session.id,
                role="assistant",
                msg_type="text",
                content=handoff_res.tourist_notice,
                timestamp=now_dt,
            )
            await self._send_whatsapp(
                instance=instance,
                recipient_number=sender_number,
                text=handoff_res.tourist_notice,
                request_id=request_id,
            )
            return RECEIPT_HANDOFF_SENTINEL

        # RF-20: Imagen que no es comprobante -> responde a la consulta o agradece y ofrece asesor
        prompt_text = (
            f"[El usuario envió una imagen con este comentario]: {caption}"
            if caption.strip()
            else "[El usuario envió una imagen sin texto]"
        )
        return await self.handle_text_message(
            tenant_id=tenant_id,
            instance=instance,
            sender_number=sender_number,
            text=prompt_text,
            request_id=request_id,
            now=now_dt,
            msg_type="image",
        )

    async def handle_text_message(
        self,
        tenant_id: str,
        instance: str,
        sender_number: str,
        text: str,
        request_id: str,
        now: datetime | None = None,
        msg_type: str = "text",
        audio_transcription: str | None = None,
    ) -> str:
        # Fallback to echo when no LLM provider is configured
        if self._llm_provider is None:
            reply_text = f"Eco [{tenant_id}]: {text}"
            await self._send_whatsapp(
                instance=instance,
                recipient_number=sender_number,
                text=reply_text,
                request_id=request_id,
            )
            return reply_text

        now_dt = now or datetime.now(timezone.utc)
        is_off_hours = self._office_hours.is_off_hours(now_dt)

        session: SessionRecord | None = None
        if self._repository is not None:
            session, is_new = await self._repository.get_or_create_active_session(
                tenant_id=tenant_id,
                phone_number=sender_number,
                now=now_dt,
            )
            if session.paused_until is not None and session.paused_until > now_dt:
                await self._repository.append_message(
                    session_id=session.id,
                    role="user",
                    msg_type=msg_type,
                    content=text,
                    audio_transcription=audio_transcription,
                    timestamp=now_dt,
                )
                logger.info(
                    "webhook_ignored_session_paused",
                    tenantId=tenant_id,
                    sessionId=session.id,
                )
                return PAUSED_BY_HANDOFF_SENTINEL

            db_messages = await self._repository.list_session_messages(session.id)
            current_history = tuple(
                ChatMessage(
                    role="model" if m.role in ("assistant", "advisor") else "user",
                    content=m.content,
                )
                for m in db_messages
            )
            assistant_messages = [m.content for m in db_messages if m.role in ("assistant", "advisor")]
            current_lead = dict(session.reservation_data or {})
            is_first_contact = is_new and len(current_history) == 0
        else:
            session_key = (tenant_id, sender_number)
            current_history = tuple(self._in_memory_history.get(session_key, []))
            assistant_messages = [
                m.content for m in current_history if m.role == "model"
            ]
            current_lead = dict(self._in_memory_leads.get(session_key, {}))
            is_first_contact = len(current_history) == 0

        tenant_config, knowledge_bundle, tour_catalog = self._get_tenant_context(
            tenant_id
        )
        input_guard = sanitize_user_input(text)

        system_prompt = build_system_prompt(
            tenant_config=tenant_config,
            knowledge_markdown=knowledge_bundle.markdown_context,
            is_first_contact=is_first_contact,
        )

        llm_response = await self._llm_provider.generate_reply(
            LLMRequest(
                tenant_id=tenant_id,
                system_prompt=system_prompt,
                user_message=input_guard.wrapped_text,
                history=current_history,
            )
        )

        parsed = _parse_llm_output(llm_response.text)
        merged_lead = _merge_lead(current_lead, parsed.lead)
        merged_lead["language"] = parsed.language

        output_guard = validate_bot_output(parsed.reply)
        final_reply = output_guard.safe_text
        all_assistant_msgs = [*assistant_messages, final_reply]

        # 1. Payment Gate Checklist enforcement (RF-09 + Anti-phishing/Premature account request)
        if parsed.intent == "pay_direct":
            gate = evaluate_payment_gate(
                lead=merged_lead,
                catalog=tour_catalog,
                assistant_messages=all_assistant_msgs,
            )
            if not gate.allowed:
                attempts = int(merged_lead.get("payment_gate_attempts") or 0) + 1
                merged_lead["payment_gate_attempts"] = attempts
                final_reply = build_payment_gate_message(
                    missing=gate.missing,
                    lead=merged_lead,
                    catalog=tour_catalog,
                    language=parsed.language,
                    blocked_attempts=attempts,
                )
                logger.warning(
                    "payment_gate_blocked",
                    tenantId=tenant_id,
                    missing=gate.missing,
                    attempts=attempts,
                )
            else:
                pay_cfg = load_payment_config(
                    tenant_id=tenant_id,
                    tenants_root=self._tenants_root,
                )
                pay_block = format_payment_message(pay_cfg, language=parsed.language)
                final_reply = f"{final_reply}\n\n{pay_block}".strip()
                if self._repository is not None and session is not None:
                    session = await self._repository.update_session(
                        session.id,
                        status="ESPERANDO_PAGO",
                        reservation_data=merged_lead,
                    )
                    await self._repository.record_event(
                        tenant_id=tenant_id,
                        session_id=session.id,
                        event_type="PAYMENT_INFO",
                        payload={"lead": merged_lead, "is_demo": pay_cfg.is_demo},
                        is_off_hours=is_off_hours,
                        timestamp=now_dt,
                    )

        # Persist lead & messages
        if self._repository is not None and session is not None:
            session = await self._repository.update_session(
                session.id,
                reservation_data=merged_lead,
            )
            await self._repository.append_message(
                session_id=session.id,
                role="user",
                msg_type=msg_type,
                content=input_guard.clean_text,
                audio_transcription=audio_transcription,
                timestamp=now_dt,
            )
            await self._repository.append_message(
                session_id=session.id,
                role="assistant",
                msg_type="text",
                content=final_reply,
                timestamp=now_dt,
            )
            await self._repository.record_event(
                tenant_id=tenant_id,
                session_id=session.id,
                event_type="TOKEN_USAGE",
                payload={"model": llm_response.model, "kind": "conversation", "intent": parsed.intent},
                tokens_in=llm_response.tokens_in,
                tokens_out=llm_response.tokens_out,
                cost_usd=llm_response.cost_usd,
                is_off_hours=is_off_hours,
                timestamp=now_dt,
            )
            if parsed.intent == "out_of_knowledge":
                # RF-05: solo se OFRECE asesor; se registra para enriquecer el knowledge (métrica)
                await self._repository.record_event(
                    tenant_id=tenant_id,
                    session_id=session.id,
                    event_type="UNANSWERED_QUESTION",
                    payload={"question": input_guard.clean_text[:500]},
                    is_off_hours=is_off_hours,
                    timestamp=now_dt,
                )
        else:
            session_key = (tenant_id, sender_number)
            self._in_memory_leads[session_key] = merged_lead
            self._in_memory_history.setdefault(session_key, []).extend(
                [
                    ChatMessage(role="user", content=input_guard.clean_text),
                    ChatMessage(role="model", content=final_reply),
                ]
            )

        # 2. Handoff to human advisor (RF-11..RF-14b)
        if (
            parsed.intent in {"human_advisor", "pqr"}
            and self._repository is not None
            and session is not None
        ):
            handoff_svc = HandoffService(
                repository=self._repository,
                whatsapp_provider=self._whatsapp_provider,
                office_hours=self._office_hours,
            )
            handoff_res = await handoff_svc.trigger(
                tenant_id=tenant_id,
                instance=instance,
                advisor_group_jid=tenant_config.advisor_group_jid,
                session=session,
                phone_number=sender_number,
                reason=parsed.intent,
                language=parsed.language,
                request_id=request_id,
                now=now_dt,
            )
            final_reply = f"{final_reply}\n\n{handoff_res.tourist_notice}".strip()

        await self._send_whatsapp(
            instance=instance,
            recipient_number=sender_number,
            text=final_reply,
            request_id=request_id,
        )

        logger.info(
            "conversation_turn_completed",
            tenantId=tenant_id,
            isFirstContact=is_first_contact,
            intent=parsed.intent,
            outputApproved=output_guard.approved,
            tokensIn=llm_response.tokens_in,
            tokensOut=llm_response.tokens_out,
            costUsd=llm_response.cost_usd,
        )
        return final_reply
