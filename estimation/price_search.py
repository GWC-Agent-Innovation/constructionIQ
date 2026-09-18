"""Interfaces for current material-price lookups."""
from __future__ import annotations

from typing import Any


class PriceSearchError(RuntimeError):
    """Raised when a price provider cannot return usable results."""


def search_prices(material: str, location: str, *, currency: str = "INR", brands: list[str] | None = None) -> list[dict[str, Any]]:
    """Search SerpApi/Tavily through backend adapters added in the next step."""
    raise NotImplementedError("Price providers are not connected yet.")
