"""Estimation orchestration boundary."""
from __future__ import annotations

from typing import Any

from .quantity_calculator import calculate_quantities
from .schemas import EstimatePreferences, EstimateResult, MaterialQuantity


def estimate_project(layout: Any, preferences: EstimatePreferences) -> EstimateResult:
    """Create an estimate result from an existing validated layout."""
    quantities = [MaterialQuantity.model_validate(item) for item in calculate_quantities(layout)]
    return EstimateResult(preferences=preferences, quantities=quantities)
