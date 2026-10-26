import asyncio
import pytest

from app.conversation.debouncer import MessageDebouncer


@pytest.mark.asyncio
async def test_debouncer_consolidates_burst_messages_within_window() -> None:
    dispatched_calls: list[tuple[str, str, str, str, str]] = []

    async def on_consolidated(
        tenant_id: str,
        instance: str,
        sender_number: str,
        combined_text: str,
        request_id: str,
    ) -> None:
        dispatched_calls.append(
            (tenant_id, instance, sender_number, combined_text, request_id)
        )

    debouncer = MessageDebouncer(
        handler=on_consolidated,
        delay_seconds=0.08,
    )

    # Enqueue 3 messages in rapid succession for the same sender
    await debouncer.enqueue(
        tenant_id="magic-tour",
        instance="magic-tour",
        sender_number="573001112233",
        text="Hola",
        request_id="req-1",
    )
    await asyncio.sleep(0.02)
    await debouncer.enqueue(
        tenant_id="magic-tour",
        instance="magic-tour",
        sender_number="573001112233",
        text="¿Cuánto cuesta Ciudad Perdida?",
        request_id="req-2",
    )
    await asyncio.sleep(0.02)
    await debouncer.enqueue(
        tenant_id="magic-tour",
        instance="magic-tour",
        sender_number="573001112233",
        text="Somos 2 personas",
        request_id="req-3",
    )

    # Before the debouncer window fires: no calls dispatched yet
    assert len(dispatched_calls) == 0

    # Wait for the debouncer window to fire (0.02 + 0.02 + 0.10 > 0.08 from last message)
    await asyncio.sleep(0.12)

    assert len(dispatched_calls) == 1
    call = dispatched_calls[0]
    assert call[0] == "magic-tour"
    assert call[2] == "573001112233"
    assert call[3] == "Hola\n¿Cuánto cuesta Ciudad Perdida?\nSomos 2 personas"
    assert call[4] == "req-3"


@pytest.mark.asyncio
async def test_debouncer_processes_independent_senders_concurrently() -> None:
    dispatched_calls: list[tuple[str, str, str]] = []

    async def on_consolidated(
        tenant_id: str,
        instance: str,
        sender_number: str,
        combined_text: str,
        request_id: str,
    ) -> None:
        dispatched_calls.append((sender_number, combined_text, request_id))

    debouncer = MessageDebouncer(
        handler=on_consolidated,
        delay_seconds=0.05,
    )

    # Two different senders
    await debouncer.enqueue(
        tenant_id="magic-tour",
        instance="magic-tour",
        sender_number="573001111111",
        text="Consulta Usuario A",
        request_id="req-A",
    )
    await debouncer.enqueue(
        tenant_id="magic-tour",
        instance="magic-tour",
        sender_number="573002222222",
        text="Consulta Usuario B",
        request_id="req-B",
    )

    await asyncio.sleep(0.10)

    assert len(dispatched_calls) == 2
    senders = {c[0] for c in dispatched_calls}
    assert senders == {"573001111111", "573002222222"}
