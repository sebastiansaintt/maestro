from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_PRICE_RE = re.compile(r"^-\s*Precio:\s*([\d\.]+)\s*COP", re.MULTILINE)


@dataclass(frozen=True)
class TourCatalogEntry:
    slug: str
    title: str
    price_text: str | None  # ej. "1.860.000" (None si la ficha no publica precio)


def load_tour_catalog(
    tenant_id: str,
    tenants_root: Path = Path("tenants"),
) -> dict[str, TourCatalogEntry]:
    """Índice slug -> (título, precio publicado) a partir de las fichas tours/*.md del tenant."""
    tours_dir = tenants_root / tenant_id / "knowledge" / "tours"
    catalog: dict[str, TourCatalogEntry] = {}
    if not tours_dir.is_dir():
        return catalog

    for md_file in sorted(tours_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        title_match = _TITLE_RE.search(content)
        price_match = _PRICE_RE.search(content)
        catalog[md_file.stem] = TourCatalogEntry(
            slug=md_file.stem,
            title=title_match.group(1).strip() if title_match else md_file.stem,
            price_text=price_match.group(1) if price_match else None,
        )
    return catalog
