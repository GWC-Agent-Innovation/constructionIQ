"""Deterministic quantity calculations will live here.

Do not put web-search or LLM logic in this file. Quantities should be
repeatable for the same layout and construction assumptions.
"""
from __future__ import annotations

from typing import Any

DEFAULTS = {
    "wall_height_ft": 10.0,
    "wall_thickness_ft": 0.5,
    "door_height_ft": 7.0,
    "window_height_ft": 4.0,
    "brick_length_ft": 0.625,
    "brick_width_ft": 0.3125,
    "brick_height_ft": 0.25,
    "brick_wastage": 0.05,
    "mortar_fraction": 0.25,
    "mortar_cement_fraction": 0.20,
    "cement_bag_volume_cuft": 1.25,
    "steel_kg_per_sqft": 4.0,
    "steel_wastage": 0.05,
}


def _value(assumptions: dict[str, float], key: str) -> float:
    value = float(assumptions.get(key, DEFAULTS[key]))
    if value < 0:
        raise ValueError(f"Assumption '{key}' cannot be negative.")
    return value


def _opening_area(layout: Any, assumptions: dict[str, float]) -> float:
    door_height = _value(assumptions, "door_height_ft")
    window_height = _value(assumptions, "window_height_ft")
    total = 0.0
    for room in layout.rooms:
        total += sum(door.width * door_height for door in room.doors)
        total += sum(window.width * window_height for window in room.windows)
    # Every interior opening is represented by both adjoining rooms.
    return total / 2.0


def calculate_quantities(layout: Any, assumptions: dict[str, float] | None = None) -> list[dict]:
    """Return repeatable preliminary quantities from a validated layout.

    These are planning quantities, not structural take-off quantities. Concrete,
    reinforcement, foundations, plumbing and electrical work need engineering
    inputs that a floor plan does not contain.
    """
    if layout is None:
        raise ValueError("A validated layout is required for material estimation.")
    values = {**DEFAULTS, **(assumptions or {})}
    floor_area = sum(room.width * room.length for room in layout.rooms)
    wall_length = sum(2 * (room.width + room.length) for room in layout.rooms) / 2.0
    wall_area = max(0.0, wall_length * _value(values, "wall_height_ft") - _opening_area(layout, values))
    wall_volume = wall_area * _value(values, "wall_thickness_ft")
    brick_volume = _value(values, "brick_length_ft") * _value(values, "brick_width_ft") * _value(values, "brick_height_ft")
    brick_count = wall_volume * (1 - _value(values, "mortar_fraction")) / brick_volume
    brick_count *= 1 + _value(values, "brick_wastage")
    mortar_volume = wall_volume * _value(values, "mortar_fraction")
    cement_masonry_bags = mortar_volume * _value(values, "mortar_cement_fraction") / _value(values, "cement_bag_volume_cuft")
    steel_kg = floor_area * _value(values, "steel_kg_per_sqft") * (1 + _value(values, "steel_wastage"))
    return [
        {"material": "Bricks / blocks", "quantity": round(brick_count), "unit": "pieces", "basis": f"{wall_volume:.1f} cu ft net masonry volume + wastage"},
        {"material": "Cement for masonry", "quantity": round(cement_masonry_bags, 2), "unit": "50 kg bags", "basis": "mortar cement fraction"},
        {"material": "Steel reinforcement bars", "quantity": round(steel_kg, 2), "unit": "kg", "basis": f"{floor_area:.1f} sq ft floor area × assumed kg/sq ft + wastage"},
    ]
