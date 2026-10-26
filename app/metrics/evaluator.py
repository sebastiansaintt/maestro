from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import yaml

from app.conversation.orchestrator import ConversationOrchestrator
from app.db.repository import SessionRepository
from app.knowledge.loader import TenantKnowledgeLoader
from app.llm.base import LLMProvider, LLMRequest, LLMResponse
from app.providers.whatsapp.base import OutboundTextMessage, WhatsAppProvider


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    language: str
    question: str
    expected_intent: str = "info"
    expected_lead: dict[str, Any] = field(default_factory=dict)
    must_contain_in_knowledge: tuple[str, ...] = ()
    must_contain_in_reply: tuple[str, ...] = ()
    must_not_contain_in_reply: tuple[str, ...] = ()
    prior_assistant_messages: tuple[str, ...] = ()
    timestamp_utc: str | None = None
    expect_handoff_event: bool = False
    expect_unanswered_event: bool = False
    simulated_llm_reply: str = ""
    critical: bool = True


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    category: str
    language: str
    critical: bool
    passed: bool
    reply: str
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalSuiteSummary:
    tenant_id: str
    total_cases: int
    passed_cases: int
    critical_total: int
    critical_passed: int
    pass_rate: float
    results: tuple[EvalCaseResult, ...]


class _NullWhatsAppProvider(WhatsAppProvider):
    def __init__(self) -> None:
        self.sent: list[OutboundTextMessage] = []

    async def send_text(self, message: OutboundTextMessage) -> dict[str, Any]:
        self.sent.append(message)
        return {"status": "SENT", "key": {"id": f"eval-{len(self.sent)}"}}


class _DeterministicEvalLLMProvider(LLMProvider):
    """En modo offline reproduce el turno estructurado JSON para validar Knowledge + Guardrails + Orquestador."""

    def __init__(self, case: EvalCase) -> None:
        self._case = case
        self.last_request: LLMRequest | None = None

    async def generate_reply(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        payload = {
            "reply": self._case.simulated_llm_reply.strip(),
            "intent": self._case.expected_intent,
            "language": self._case.language,
            "lead": self._case.expected_lead,
        }
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            model="eval-deterministic-gemini-3-flash",
            tokens_in=250,
            tokens_out=60,
            cost_usd=0.0003,
            duration_ms=12.5,
        )


def load_eval_suite(path: Path) -> list[EvalCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases_raw = raw.get("cases", [])
    cases: list[EvalCase] = []
    for item in cases_raw:
        cases.append(
            EvalCase(
                id=str(item["id"]),
                category=str(item["category"]),
                language=str(item.get("language", "es")),
                question=str(item["question"]),
                expected_intent=str(item.get("expected_intent", "info")),
                expected_lead=dict(item.get("expected_lead") or {}),
                must_contain_in_knowledge=tuple(item.get("must_contain_in_knowledge") or ()),
                must_contain_in_reply=tuple(item.get("must_contain_in_reply") or ()),
                must_not_contain_in_reply=tuple(item.get("must_not_contain_in_reply") or ()),
                prior_assistant_messages=tuple(item.get("prior_assistant_messages") or ()),
                timestamp_utc=item.get("timestamp_utc"),
                expect_handoff_event=bool(item.get("expect_handoff_event", False)),
                expect_unanswered_event=bool(item.get("expect_unanswered_event", False)),
                simulated_llm_reply=str(item.get("simulated_llm_reply", "")),
                critical=bool(item.get("critical", True)),
            )
        )
    return cases


class EvalRunner:
    """Ejecuta la suite de evaluación contra el Knowledge Base real y el ConversationOrchestrator."""

    def __init__(
        self,
        tenant_id: str = "magic-tour",
        tenants_root: Path = Path("tenants"),
        db_dir: Path | None = None,
        live_llm_provider: LLMProvider | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._tenants_root = tenants_root
        self._db_dir = db_dir or Path(".eval_tmp")
        self._live_llm = live_llm_provider
        self._knowledge_bundle = TenantKnowledgeLoader(
            tenants_root=tenants_root
        ).load_tenant_knowledge(tenant_id)

    async def run_case(self, case: EvalCase, db_path: Path) -> EvalCaseResult:
        failures: list[str] = []
        kb_text_lower = self._knowledge_bundle.markdown_context.lower()

        # 1. Verificar que los datos de verdad (precios, cierres, políticas) existen en el Knowledge Base real
        for needle in case.must_contain_in_knowledge:
            if needle.lower() not in kb_text_lower:
                failures.append(f"Knowledge base missing expected fact: '{needle}'")

        repo = SessionRepository(db_path=db_path)
        await repo.init_db()

        now_dt = (
            datetime.fromisoformat(case.timestamp_utc.replace("Z", "+00:00"))
            if case.timestamp_utc
            else datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
        )
        phone = f"57300{abs(hash(case.id)) % 10_000_000:07d}"

        if case.prior_assistant_messages:
            session, _ = await repo.get_or_create_active_session(
                tenant_id=self._tenant_id,
                phone_number=phone,
                now=now_dt,
            )
            for msg in case.prior_assistant_messages:
                await repo.append_message(
                    session_id=session.id,
                    role="assistant",
                    msg_type="text",
                    content=msg,
                    timestamp=now_dt,
                )

        whatsapp = _NullWhatsAppProvider()
        llm = self._live_llm or _DeterministicEvalLLMProvider(case)
        orchestrator = ConversationOrchestrator(
            whatsapp_provider=whatsapp,
            llm_provider=llm,
            tenants_root=self._tenants_root,
            repository=repo,
        )

        reply = await orchestrator.handle_text_message(
            tenant_id=self._tenant_id,
            instance="magic-tour-instance",
            sender_number=phone,
            text=case.question,
            request_id=f"eval-{case.id}",
            now=now_dt,
        )

        reply_lower = reply.lower()
        for expected in case.must_contain_in_reply:
            if expected.lower() not in reply_lower:
                failures.append(f"Reply missing '{expected}' (got: {reply!r})")

        for forbidden in case.must_not_contain_in_reply:
            if forbidden.lower() in reply_lower:
                failures.append(f"Reply contained forbidden '{forbidden}' (got: {reply!r})")

        if case.expect_handoff_event:
            events = await repo.list_events(tenant_id=self._tenant_id, event_type="HANDOFF")
            if not events:
                failures.append("Expected HANDOFF event to be recorded, but none found")

        if case.expect_unanswered_event:
            events = await repo.list_events(
                tenant_id=self._tenant_id, event_type="UNANSWERED_QUESTION"
            )
            if not events:
                failures.append(
                    "Expected UNANSWERED_QUESTION event to be recorded, but none found"
                )

        return EvalCaseResult(
            case_id=case.id,
            category=case.category,
            language=case.language,
            critical=case.critical,
            passed=len(failures) == 0,
            reply=reply,
            failures=tuple(failures),
        )

    async def run_suite(self, cases: list[EvalCase]) -> EvalSuiteSummary:
        self._db_dir.mkdir(parents=True, exist_ok=True)
        results: list[EvalCaseResult] = []
        for idx, case in enumerate(cases):
            db_path = self._db_dir / f"eval_{idx}_{case.id}.sqlite3"
            if db_path.exists():
                db_path.unlink()
            res = await self.run_case(case, db_path=db_path)
            results.append(res)

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        crit_total = sum(1 for r in results if r.critical)
        crit_passed = sum(1 for r in results if r.critical and r.passed)
        rate = round(passed / total, 4) if total else 0.0

        return EvalSuiteSummary(
            tenant_id=self._tenant_id,
            total_cases=total,
            passed_cases=passed,
            critical_total=crit_total,
            critical_passed=crit_passed,
            pass_rate=rate,
            results=tuple(results),
        )
