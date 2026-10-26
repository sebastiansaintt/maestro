from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import time

from app.core.logging import get_logger

logger = get_logger()

_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9-]+$")
_CATEGORY_ORDER: tuple[str, ...] = (
    "agencia",
    "politicas",
    "faq",
    "tips",
    "tours",
)


class KnowledgeAccessError(Exception):
    """Raised when a tenant knowledge directory is invalid or attempts path traversal."""


@dataclass(frozen=True)
class TenantKnowledgeBundle:
    tenant_id: str
    markdown_context: str
    file_count: int
    total_chars: int
    categories: dict[str, list[str]] = field(default_factory=dict)


class TenantKnowledgeLoader:
    """Loads and isolates Markdown knowledge base files per tenant (SEC-02)."""

    def __init__(self, tenants_root: Path = Path("tenants")) -> None:
        self._tenants_root = tenants_root.resolve()

    def _resolve_knowledge_dir(self, tenant_id: str) -> Path:
        if not tenant_id or not _TENANT_ID_PATTERN.match(tenant_id):
            logger.warning(
                "knowledge_access_denied",
                tenantId=tenant_id,
                reason="invalid_tenant_id_format",
            )
            raise KnowledgeAccessError(f"Invalid tenant_id format: {tenant_id!r}")

        candidate = (self._tenants_root / tenant_id / "knowledge").resolve()
        if not candidate.is_relative_to(self._tenants_root):
            logger.warning(
                "knowledge_access_denied",
                tenantId=tenant_id,
                reason="path_traversal_detected",
            )
            raise KnowledgeAccessError(f"Path traversal detected for tenant: {tenant_id!r}")

        if not candidate.exists() or not candidate.is_dir():
            logger.warning(
                "knowledge_access_denied",
                tenantId=tenant_id,
                reason="tenant_knowledge_not_found",
            )
            raise KnowledgeAccessError(
                f"Knowledge base directory not found for tenant: {tenant_id!r}"
            )

        return candidate

    def load_tenant_knowledge(self, tenant_id: str) -> TenantKnowledgeBundle:
        started_at = time.perf_counter()
        knowledge_dir = self._resolve_knowledge_dir(tenant_id)

        sections: list[str] = []
        categories: dict[str, list[str]] = {}
        file_count = 0

        for category in _CATEGORY_ORDER:
            cat_dir = knowledge_dir / category
            if not cat_dir.exists() or not cat_dir.is_dir():
                continue

            md_files = sorted(cat_dir.glob("*.md"), key=lambda p: p.name)
            if not md_files:
                continue

            categories[category] = [f.name for f in md_files]
            for md_file in md_files:
                content = md_file.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                rel_path = f"{category}/{md_file.name}"
                sections.append(
                    f"<!-- SOURCE_FILE: {rel_path} -->\n{content}\n<!-- END_SOURCE_FILE: {rel_path} -->"
                )
                file_count += 1

        markdown_context = "\n\n---\n\n".join(sections)
        total_chars = len(markdown_context)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)

        logger.info(
            "knowledge_loaded",
            tenantId=tenant_id,
            fileCount=file_count,
            totalChars=total_chars,
            durationMs=duration_ms,
        )

        return TenantKnowledgeBundle(
            tenant_id=tenant_id,
            markdown_context=markdown_context,
            file_count=file_count,
            total_chars=total_chars,
            categories=categories,
        )
