"""Targeted room edits: resize or swap two rooms with no LLM call.

Both operate directly on the final compiled rectangles (Layout.rooms), the
only geometry that is actually persisted - there is no partition tree to
mutate for layouts produced by the standard solver fallback, and the tree
used by an AI-compiled layout is discarded once compiled. Working on
rectangles keeps this uniform for either origin.

Both replan doors/windows from scratch (room adjacency may have changed)
and run the same validator every AI/solver layout must pass, so a resize or
swap that breaks circulation, room proportions or a bathroom/parking cap is
rejected with a clear message rather than silently applied.
"""
from __future__ import annotations

from agent.ai_arrangements import room_catalog
from agent.door_planner import plan_openings
from agent.openings import WALLS, shared_span, wall_span
from agent.schemas import HouseSpec, Layout
from agent.validator import validate_layout

FALLBACK_MINIMUM_AREA_SQFT = 25.0


def _minimum_area_sqft(spec: HouseSpec, room_id: str) -> float:
    """Catalog minimums (e.g. a bedroom needs >=90 sq ft) aren't re-checked by
    validate_layout once a layout exists - only the generic 5ft/side and
    aspect-ratio checks are. A resize/swap must still respect them."""
    entry = room_catalog(spec).get(room_id)
    return entry["minimum_area_sqft"] if entry else FALLBACK_MINIMUM_AREA_SQFT


def resize_room(layout: Layout, spec: HouseSpec, room_id: str, edge: str,
                delta_ft: float) -> tuple[Layout | None, list[str]]:
    """Move the wall on `room_id`'s `edge` by `delta_ft` (positive grows the
    room, taking the space from whichever single neighbor shares that whole
    wall) and replan/validate the result. The plot stays fully tiled by
    construction: the two rectangles only trade a strip along their shared
    boundary, nothing else moves."""
    if edge not in WALLS:
        return None, [f"Unknown wall: {edge}."]
    if delta_ft == 0:
        return None, ["No change requested."]
    working = layout.model_copy(deep=True)
    by_id = {room.id: room for room in working.rooms}
    room = by_id.get(room_id)
    if room is None:
        return None, [f"Unknown room: {room_id}."]

    _, _, start, end = wall_span(room, edge)
    neighbor = None
    for other in working.rooms:
        if other.id == room.id or other.floor != room.floor:
            continue
        span = shared_span(room, edge, other)
        if span and abs(span[0] - start) < 1e-6 and abs(span[1] - end) < 1e-6:
            neighbor = other
            break
    if neighbor is None:
        return None, [f"{room.name}'s {edge} wall isn't one straight shared boundary with a "
                      "single neighboring room, so it can't be resized directly."]

    if edge == "north":
        room.y -= delta_ft
        room.length += delta_ft
        neighbor.length -= delta_ft
    elif edge == "south":
        room.length += delta_ft
        neighbor.y += delta_ft
        neighbor.length -= delta_ft
    elif edge == "west":
        room.x -= delta_ft
        room.width += delta_ft
        neighbor.width -= delta_ft
    else:  # east
        room.width += delta_ft
        neighbor.x += delta_ft
        neighbor.width -= delta_ft

    for target in (room, neighbor):
        if target.width <= 0 or target.length <= 0:
            return None, [f"That resize would leave {target.name} with no area left."]
        area = target.width * target.length
        minimum = _minimum_area_sqft(spec, target.id)
        if area < minimum - 1e-6:
            return None, [f"{target.name} would drop to {area:.1f} sq ft, below its "
                          f"{minimum:g} sq ft minimum."]

    try:
        working = plan_openings(working, spec)
    except ValueError as exc:
        return None, [str(exc)]
    issues = validate_layout(working, spec)
    if issues:
        return None, [issue.message for issue in issues][:8]
    working.planning_notes = [f"Manually resized {room.name} ({edge} edge, {delta_ft:+g} ft).",
                              *working.planning_notes][:6]
    return working, []


def swap_rooms(layout: Layout, spec: HouseSpec, room_id_a: str,
               room_id_b: str) -> tuple[Layout | None, list[str]]:
    """Exchange the rectangles (position/size) of two rooms, keeping each
    room's own identity (id, name, ensuite_for) - "switching" which spot in
    the house each room occupies, not what either room is."""
    if room_id_a == room_id_b:
        return None, ["Choose two different rooms to swap."]
    working = layout.model_copy(deep=True)
    by_id = {room.id: room for room in working.rooms}
    room_a, room_b = by_id.get(room_id_a), by_id.get(room_id_b)
    if room_a is None or room_b is None:
        return None, ["Unknown room in swap request."]

    (room_a.x, room_a.y, room_a.width, room_a.length,
     room_b.x, room_b.y, room_b.width, room_b.length) = (
        room_b.x, room_b.y, room_b.width, room_b.length,
        room_a.x, room_a.y, room_a.width, room_a.length,
    )
    if room_a.floor != room_b.floor:
        room_a.floor, room_b.floor = room_b.floor, room_a.floor

    for room in (room_a, room_b):
        area = room.width * room.length
        minimum = _minimum_area_sqft(spec, room.id)
        if area < minimum - 1e-6:
            return None, [f"{room.name} needs at least {minimum:g} sq ft; its new spot "
                          f"is only {area:.1f} sq ft."]

    try:
        working = plan_openings(working, spec)
    except ValueError as exc:
        return None, [str(exc)]
    issues = validate_layout(working, spec)
    if issues:
        return None, [issue.message for issue in issues][:8]
    working.planning_notes = [f"Manually swapped {room_a.name} and {room_b.name}.",
                              *working.planning_notes][:6]
    return working, []
