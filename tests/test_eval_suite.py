from __future__ import annotations

from pathlib import Path
import pytest

from app.metrics.evaluator import EvalCase, EvalCaseResult, EvalRunner, EvalSuiteSummary, load_eval_suite


def test_magic_tour_eval_yaml_has_at_least_40_cases_across_required_categories() -> None:
    suite_path = Path("evals/magic-tour.yaml")
    cases = load_eval_suite(suite_path)

    assert len(cases) >= 40, f"Expected at least 40 eval cases, got {len(cases)}"

    categories = {c.category for c in cases}
    required_categories = {
        "exact_price",
        "itinerary_and_includes",
        "closure_dates",
        "policy_and_faq",
        "trap_discount",
        "trap_out_of_catalog",
        "payment_gate_block",
        "payment_gate_unlock",
        "handoff_and_off_hours",
        "unsupported_language",
    }
    assert required_categories.issubset(categories), (
        f"Missing required categories: {required_categories - categories}"
    )

    languages = {c.language for c in cases}
    assert {"es", "en"}.issubset(languages)


@pytest.mark.asyncio
async def test_eval_runner_executes_all_magic_tour_cases_with_100_percent_critical_pass_rate(
    tmp_path: Path,
) -> None:
    suite_path = Path("evals/magic-tour.yaml")
    cases = load_eval_suite(suite_path)

    runner = EvalRunner(
        tenant_id="magic-tour",
        tenants_root=Path("tenants"),
        db_dir=tmp_path,
    )
    summary: EvalSuiteSummary = await runner.run_suite(cases)

    assert summary.total_cases >= 40
    assert summary.critical_total >= 40
    assert summary.critical_passed == summary.critical_total, (
        "Critical eval failures:\n"
        + "\n".join(
            f"- {r.case_id} ({r.category}): {r.failures}"
            for r in summary.results
            if not r.passed and r.critical
        )
    )
    assert summary.pass_rate == 1.0
