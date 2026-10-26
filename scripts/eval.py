from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import tempfile

# Allow running directly via `python scripts/eval.py`
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings
from app.llm.gemini import GeminiLLMProvider
from app.metrics.evaluator import EvalRunner, load_eval_suite


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta la suite de evaluación de Maestro.")
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("evals/magic-tour.yaml"),
        help="Ruta al archivo YAML de evaluación.",
    )
    parser.add_argument(
        "--tenant",
        type=str,
        default="magic-tour",
        help="Identificador del tenant.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Usa Gemini 3.8 Flash en vivo en lugar del proveedor determinista offline.",
    )
    args = parser.parse_args()

    cases = load_eval_suite(args.suite)
    live_llm = None
    if args.live:
        live_llm = GeminiLLMProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        runner = EvalRunner(
            tenant_id=args.tenant,
            tenants_root=Path("tenants"),
            db_dir=Path(tmp_dir),
            live_llm_provider=live_llm,
        )
        summary = await runner.run_suite(cases)

    print(f"=== Evaluación Maestro — Tenant: {summary.tenant_id} ===")
    print(f"Casos totales  : {summary.passed_cases}/{summary.total_cases} ({summary.pass_rate * 100:.1f}%)")
    print(f"Casos críticos : {summary.critical_passed}/{summary.critical_total}")

    failed = [r for r in summary.results if not r.passed]
    if failed:
        print("\nFallos detectados:")
        for r in failed:
            print(f"  [FAIL] {r.case_id} ({r.category}):")
            for f in r.failures:
                print(f"    - {f}")
        return 1

    print("\n[OK] 100% de aciertos en preguntas críticas y compuertas de seguridad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
