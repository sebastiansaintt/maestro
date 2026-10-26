from pathlib import Path
import pytest

from app.knowledge.loader import KnowledgeAccessError, TenantKnowledgeLoader


def test_loader_concatenates_markdown_deterministically_and_blocks_path_traversal(
    tmp_path: Path,
) -> None:
    tenant_dir = tmp_path / "magic-tour" / "knowledge"
    (tenant_dir / "agencia").mkdir(parents=True)
    (tenant_dir / "politicas").mkdir(parents=True)
    (tenant_dir / "faq").mkdir(parents=True)
    (tenant_dir / "tips").mkdir(parents=True)
    (tenant_dir / "tours").mkdir(parents=True)

    (tenant_dir / "agencia" / "sobre-nosotros.md").write_text(
        "# Sobre Nosotros\nAgencia en Santa Marta RNT 30835.", encoding="utf-8"
    )
    (tenant_dir / "politicas" / "terminos.md").write_text(
        "# Términos\nPolítica de cancelación.", encoding="utf-8"
    )
    (tenant_dir / "faq" / "preguntas.md").write_text(
        "# FAQ\n¿Cómo reservar?", encoding="utf-8"
    )
    (tenant_dir / "tips" / "tayrona.md").write_text(
        "# Destino Tayrona\nLlevar documento.", encoding="utf-8"
    )
    (tenant_dir / "tours" / "ciudad-perdida.md").write_text(
        "# Ciudad Perdida\nPrecio: 1.860.000 COP", encoding="utf-8"
    )

    loader = TenantKnowledgeLoader(tenants_root=tmp_path)
    bundle = loader.load_tenant_knowledge("magic-tour")

    assert bundle.tenant_id == "magic-tour"
    assert bundle.file_count == 5
    assert bundle.total_chars == len(bundle.markdown_context)
    assert "RNT 30835" in bundle.markdown_context
    assert "1.860.000 COP" in bundle.markdown_context

    # Deterministic ordering: agencia comes before tours
    assert bundle.markdown_context.index("RNT 30835") < bundle.markdown_context.index(
        "1.860.000 COP"
    )

    # Path traversal and unknown tenant rejection (SEC-02)
    with pytest.raises(KnowledgeAccessError):
        loader.load_tenant_knowledge("../other-tenant")

    with pytest.raises(KnowledgeAccessError):
        loader.load_tenant_knowledge("non-existent-tenant")
