import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger()


@dataclass
class LatencyHistogram:
    """Histograma RED con etiquetas acotadas (bounded labels) y cálculo de percentiles p50/p95/p99."""

    samples: dict[tuple[str, ...], list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def observe(self, labels: tuple[str, ...], duration_ms: float) -> None:
        self.samples[labels].append(duration_ms)

    def summary(self, labels: tuple[str, ...]) -> dict[str, float]:
        values = sorted(self.samples.get(labels, []))
        if not values:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
        n = len(values)

        def percentile(p: float) -> float:
            idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
            return round(values[idx], 2)

        return {
            "count": n,
            "p50": percentile(50),
            "p95": percentile(95),
            "p99": percentile(99),
        }


http_request_duration_ms = LatencyHistogram()
external_call_duration_ms = LatencyHistogram()
