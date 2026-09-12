"""Physical wall intervals shared by planning, validation and SVG rendering.

All coordinates and opening widths are in feet. An interior opening is stored
on both rooms, at the same world coordinates, for backwards-compatible JSON.
"""
from __future__ import annotations

from agent.schemas import Door, Layout, Room, Window

OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
WALLS = tuple(OPPOSITE)
EPS = 0.01
JAMB = 0.4


def wall_span(room: Room, wall: str) -> tuple[str, float, float, float]:
    """Return axis (h/v), fixed coordinate, interval start/end."""
    if wall in ("north", "south"):
        return "h", room.y + (room.length if wall == "south" else 0), room.x, room.x + room.width
    return "v", room.x + (room.width if wall == "east" else 0), room.y, room.y + room.length


def shared_span(a: Room, wall: str, b: Room) -> tuple[float, float] | None:
    axis, fixed, start, end = wall_span(a, wall)
    other_axis, other_fixed, other_start, other_end = wall_span(b, OPPOSITE[wall])
    if a.floor != b.floor or axis != other_axis or abs(fixed - other_fixed) > EPS:
        return None
    lo, hi = max(start, other_start), min(end, other_end)
    return (lo, hi) if hi > lo else None


def opening_span(room: Room, opening: Door | Window) -> tuple[str, float, float, float]:
    axis, fixed, start, end = wall_span(room, opening.wall)
    center = start + (end - start) * opening.position
    return axis, fixed, center - opening.width / 2, center + opening.width / 2


def exterior(layout: Layout, room: Room, wall: str) -> bool:
    _, fixed, _, _ = wall_span(room, wall)
    boundary = {"north": 0, "west": 0, "south": layout.plot_length_ft, "east": layout.plot_width_ft}[wall]
    return abs(fixed - boundary) <= EPS


def subtract_intervals(start: float, end: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result = []
    cursor = start
    for lo, hi in sorted(cuts):
        lo, hi = max(start, lo), min(end, hi)
        if hi <= lo or hi <= cursor:
            continue
        if lo > cursor:
            result.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < end:
        result.append((cursor, end))
    return result


def connect(a: Room, wall: str, b: Room, kind: str = "door", near_start: bool = False) -> None:
    span = shared_span(a, wall, b)
    if span is None or span[1] - span[0] < 2.8 + 2 * JAMB:
        raise ValueError(f"{a.name} and {b.name} need at least 3.6 ft of shared wall.")
    lo, hi = span
    width = hi - lo - 2 * JAMB if kind == "opening" else 2.8
    center = lo + JAMB + width / 2 if near_start else (lo + hi) / 2
    for room, side, target in ((a, wall, b), (b, OPPOSITE[wall], a)):
        _, _, start, end = wall_span(room, side)
        room.doors.append(Door(wall=side, position=(center - start) / (end - start),
                               connects_to=target.id, width=width, kind=kind))


def add_windows(layout: Layout) -> None:
    """Place one window per exposed room, in a free exterior wall interval."""
    for room in layout.rooms:
        choices = []
        for wall in WALLS:
            if not exterior(layout, room, wall):
                continue
            _, _, start, end = wall_span(room, wall)
            cuts = [(opening_span(room, d)[2] - JAMB, opening_span(room, d)[3] + JAMB)
                    for d in room.doors if d.wall == wall]
            for lo, hi in subtract_intervals(start + JAMB, end - JAMB, cuts):
                if hi - lo >= 2:
                    choices.append((hi - lo, wall, lo, hi, start, end))
        if choices:
            _, wall, lo, hi, start, end = max(choices)
            width = min(4.0, hi - lo)
            room.windows.append(Window(wall=wall, width=width,
                                       position=((lo + hi) / 2 - start) / (end - start)))

