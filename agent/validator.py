"""Deterministic checks the LLM-proposed layout must pass before it's accepted.

The LLM is good at "kitchen should be near the entrance" but unreliable at
exact geometry. These checks catch what it typically gets wrong: overlaps,
rooms outside the plot, rooms nobody can actually get into, and doors that
claim a connection that isn't physically real.

Split into geometry checks (room shapes/positions only) and door checks
(reachability/circulation), so generation can happen in two focused stages:
place rooms first, then wire up doors using real computed adjacency.
"""
from __future__ import annotations

import re

from agent.schemas import Door, HouseSpec, Layout, Room, ValidationIssue
from agent.openings import EPS, JAMB, OPPOSITE, WALLS, exterior, opening_span, shared_span, wall_span

MIN_ROOM_DIMENSION_FT = 5.0
MAX_ASPECT_RATIO = 2.2  # longer side : shorter side â€” beyond this a room reads as an unnatural strip
MIN_SHARED_WALL_FT = 3.6  # 2.8 ft opening plus two 0.4 ft jambs
ADJACENCY_TOLERANCE_FT = 0.01  # float slop when comparing edges for exact touching

PUBLIC_KEYWORDS = ("living", "dining", "hall", "corridor", "passage", "foyer", "veranda", "lounge", "family")
BEDROOM_KEYWORDS = ("bed",)
BATHROOM_KEYWORDS = ("bath", "toilet", "washroom", "wc")
PARKING_KEYWORDS = ("parking", "garage", "carport", "driveway")


def room_category(name: str) -> str:
    n = name.lower()
    if any(k in n for k in BATHROOM_KEYWORDS):
        return "bathroom"
    if any(k in n for k in BEDROOM_KEYWORDS):
        return "bedroom"
    if any(k in n for k in PARKING_KEYWORDS):
        return "parking"  # needs its own exterior/driveway door, not just an interior one
    if any(k in n for k in PUBLIC_KEYWORDS):
        return "public"
    return "private"  # kitchen, office, pooja, utility, store, etc.


def _rects_overlap(a: Room, b: Room) -> bool:
    return (
        a.x < b.x + b.width
        and a.x + a.width > b.x
        and a.y < b.y + b.length
        and a.y + a.length > b.y
    )


def _range_overlap(a1: float, a2: float, b1: float, b2: float) -> float:
    return min(a2, b2) - max(a1, b1)


def validate_geometry(layout: Layout) -> list[ValidationIssue]:
    """Checks that only need room shapes/positions: no doors involved yet."""
    issues: list[ValidationIssue] = []
    rooms = layout.rooms

    if not rooms:
        return [ValidationIssue(message="layout has no rooms")]

    ids = [r.id for r in rooms]
    if len(ids) != len(set(ids)) or "outside" in ids:
        issues.append(ValidationIssue(message="Room IDs must be unique and cannot be 'outside'."))

    for room in rooms:
        if room.width < MIN_ROOM_DIMENSION_FT or room.length < MIN_ROOM_DIMENSION_FT:
            issues.append(ValidationIssue(
                room_id=room.id,
                message=f"{room.name} ({room.id}) is {room.width}x{room.length}ft, "
                        f"smaller than the {MIN_ROOM_DIMENSION_FT}ft minimum.",
            ))
        if room.x < 0 or room.y < 0 or room.x + room.width > layout.plot_width_ft \
                or room.y + room.length > layout.plot_length_ft:
            issues.append(ValidationIssue(
                room_id=room.id,
                message=f"{room.name} ({room.id}) falls outside the "
                        f"{layout.plot_width_ft}x{layout.plot_length_ft}ft plot boundary.",
            ))
        longer, shorter = max(room.width, room.length), min(room.width, room.length)
        is_corridor = any(k in room.name.lower() for k in ("hall", "corridor", "passage"))
        if not is_corridor and shorter > 0 and longer / shorter > MAX_ASPECT_RATIO:
            issues.append(ValidationIssue(
                room_id=room.id,
                message=f"{room.name} ({room.id}) is {room.width}x{room.length}ft â€” too "
                        f"elongated (ratio {longer / shorter:.1f}:1, max {MAX_ASPECT_RATIO}:1). "
                        f"Reshape it closer to square, or split the leftover space into a "
                        f"separate passage/corridor room instead of stretching this one.",
            ))

    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            if a.floor == b.floor and _rects_overlap(a, b):
                issues.append(ValidationIssue(
                    room_id=a.id,
                    message=f"{a.name} ({a.id}) overlaps {b.name} ({b.id}).",
                ))

    plot_area = layout.plot_width_ft * layout.plot_length_ft
    for floor in {r.floor for r in rooms}:
        covered = sum(r.width * r.length for r in rooms if r.floor == floor)
        gap = plot_area - covered
        if gap > 1.0:
            issues.append(ValidationIssue(
                message=f"Floor {floor}: rooms cover only {covered:g} of {plot_area:g} sqft "
                        f"â€” {gap:g} sqft of the plot is empty floor space. Every room must tile "
                        f"the plot completely with no leftover gaps: resize/reposition rooms (or "
                        f"add a room, e.g. a passage/store room) to fill it entirely.",
            ))

    return issues


def validate_circulation_feasibility(layout: Layout) -> list[ValidationIssue]:
    """Geometry-only check: can this room placement even support proper
    circulation, before we bother asking a model to place doors on it?

    A door can only go where two rooms are actually adjacent, so if a
    private room's placement leaves it with no wall touching any public
    room (or, for a bathroom, its own bedroom), no door arrangement in the
    next stage can fix it â€” the room placement itself is unsalvageable and
    needs to be redone, not patched with more door retries.
    """
    issues: list[ValidationIssue] = []
    rooms = layout.rooms
    room_by_id = {r.id: r for r in rooms}
    adjacency = compute_adjacency(layout)

    public_ids = {r.id for r in rooms if room_category(r.name) == "public"}
    if public_ids:
        reached = {next(iter(public_ids))}
        pending = list(reached)
        while pending:
            current = pending.pop()
            neighbors = {n for edge in adjacency[current].values() for n in edge} & public_ids
            for neighbor in neighbors - reached:
                reached.add(neighbor)
                pending.append(neighbor)
        if reached != public_ids:
            issues.append(ValidationIssue(message="Public spaces are disconnected. Join every living/dining/hallway zone using shared walls at least 3.6 ft long; do not route circulation through a kitchen or bedroom."))

    for room in rooms:
        category = room_category(room.name)
        if category == "public":
            continue
        if category == "parking":
            if not any(exterior(layout, room, wall) for wall in WALLS):
                issues.append(ValidationIssue(
                    room_id=room.id,
                    message=f"{room.name} ({room.id}) doesn't touch the plot boundary â€” parking needs "
                            f"its own exterior wall for a direct driveway door, not just an interior "
                            f"door through another room. Reposition it against the plot edge.",
                ))
            continue

        allowed_categories = {"public", "bedroom"} if category == "bathroom" else {"public"}
        neighbor_ids = {n for wall_list in adjacency[room.id].values() for n in wall_list}
        has_feasible_neighbor = any(
            n in room_by_id and room_category(room_by_id[n].name) in allowed_categories
            for n in neighbor_ids
        )
        if not has_feasible_neighbor:
            issues.append(ValidationIssue(
                room_id=room.id,
                message=f"{room.name} ({room.id}) doesn't touch any wall of a shared space "
                        f"(living/dining/hallway){' or a bedroom' if category == 'bathroom' else ''} "
                        f"â€” its placement makes it geometrically impossible to give it a working door "
                        f"later. Reposition rooms so {room.name} shares a wall with the living/dining "
                        f"area{' or a bedroom' if category == 'bathroom' else ''}, even if that means "
                        f"adding connected rectangular hallway zones so they "
                        f"touch every bedroom and the kitchen. Each individual room must remain rectangular.",
            ))

    return issues


def compute_adjacency(layout: Layout) -> dict[str, dict[str, list[str]]]:
    """For each room, which real neighbor (or 'outside') borders each wall.

    Used both to hand the door-placement model a list of *valid* choices
    (so it can't invent a connection that isn't physically there) and to
    verify the doors it comes back with are real.
    """
    rooms = layout.rooms
    candidates: dict[str, dict[str, list[str]]] = {
        r.id: {"north": [], "south": [], "east": [], "west": []} for r in rooms
    }

    for room in rooms:
        if abs(room.x) < ADJACENCY_TOLERANCE_FT:
            candidates[room.id]["west"].append("outside")
        if abs(room.x + room.width - layout.plot_width_ft) < ADJACENCY_TOLERANCE_FT:
            candidates[room.id]["east"].append("outside")
        if abs(room.y) < ADJACENCY_TOLERANCE_FT:
            candidates[room.id]["north"].append("outside")
        if abs(room.y + room.length - layout.plot_length_ft) < ADJACENCY_TOLERANCE_FT:
            candidates[room.id]["south"].append("outside")

    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            if a.floor != b.floor:
                continue
            if abs((a.x + a.width) - b.x) < ADJACENCY_TOLERANCE_FT and \
                    _range_overlap(a.y, a.y + a.length, b.y, b.y + b.length) >= MIN_SHARED_WALL_FT:
                candidates[a.id]["east"].append(b.id)
                candidates[b.id]["west"].append(a.id)
            elif abs((b.x + b.width) - a.x) < ADJACENCY_TOLERANCE_FT and \
                    _range_overlap(a.y, a.y + a.length, b.y, b.y + b.length) >= MIN_SHARED_WALL_FT:
                candidates[a.id]["west"].append(b.id)
                candidates[b.id]["east"].append(a.id)
            elif abs((a.y + a.length) - b.y) < ADJACENCY_TOLERANCE_FT and \
                    _range_overlap(a.x, a.x + a.width, b.x, b.x + b.width) >= MIN_SHARED_WALL_FT:
                candidates[a.id]["south"].append(b.id)
                candidates[b.id]["north"].append(a.id)
            elif abs((b.y + b.length) - a.y) < ADJACENCY_TOLERANCE_FT and \
                    _range_overlap(a.x, a.x + a.width, b.x, b.x + b.width) >= MIN_SHARED_WALL_FT:
                candidates[a.id]["north"].append(b.id)
                candidates[b.id]["south"].append(a.id)

    return candidates


def _validate_door_targets(layout: Layout) -> list[ValidationIssue]:
    """Checks that need doors: reachability, circulation, and real adjacency."""
    issues: list[ValidationIssue] = []
    rooms = layout.rooms
    room_by_id = {r.id: r for r in rooms}
    adjacency = compute_adjacency(layout)

    for room in rooms:
        if not room.doors:
            issues.append(ValidationIssue(
                room_id=room.id,
                message=f"{room.name} ({room.id}) has no door, so it isn't reachable.",
            ))
        for door in room.doors:
            valid_targets = adjacency.get(room.id, {}).get(door.wall, [])
            if not door.connects_to or door.connects_to not in valid_targets:
                issues.append(ValidationIssue(
                    room_id=room.id,
                    message=f"{room.name} ({room.id})'s {door.wall} door claims connects_to="
                            f"'{door.connects_to}', but that room isn't actually adjacent on that "
                            f"wall. Real options for {room.name}'s {door.wall} wall: "
                            f"{valid_targets or 'none â€” pick a different wall'}.",
                ))
            elif door.connects_to and door.connects_to != "outside" and door.connects_to not in room_by_id:
                issues.append(ValidationIssue(
                    room_id=room.id,
                    message=f"{room.name} ({room.id}) has a door to unknown room '{door.connects_to}'.",
                ))

    # Circulation: a private room reachable only by cutting through another
    # private room (e.g. a bedroom only accessible via a different bedroom, or
    # a bathroom only accessible via the kitchen) has no real walking path.
    # Every non-public room needs a door straight to a shared/public space â€”
    # an exterior door alone doesn't count: a bedroom with only its own door
    # to outside is a separate cottage, not a room connected to the house.
    # A bathroom may instead (or also) connect directly to its own bedroom.
    for room in rooms:
        category = room_category(room.name)
        if category == "public":
            continue

        targets = [d.connects_to for d in room.doors if d.connects_to]
        allowed_categories = {"public", "bedroom"} if category == "bathroom" else {"public"}
        has_clean_path = any(
            t in room_by_id and room_category(room_by_id[t].name) in allowed_categories
            for t in targets
        )
        if not has_clean_path:
            issues.append(ValidationIssue(
                room_id=room.id,
                message=f"{room.name} ({room.id}) has no direct door to a shared interior space "
                        f"(living/dining/hallway){' or its own bedroom' if category == 'bathroom' else ''} "
                        f"â€” a door to outside doesn't count, and it's currently only reachable by "
                        f"cutting through another private room, which breaks circulation. Add a door "
                        f"straight from {room.name} to a public room{' or its bedroom' if category == 'bathroom' else ''}.",
            ))

    return issues


def validate_doors(layout: Layout) -> list[ValidationIssue]:
    issues = _validate_door_targets(layout)
    by_id = {r.id: r for r in layout.rooms}
    graph = {r.id: set() for r in layout.rooms}
    entrances = set()
    for room in layout.rooms:
        intervals = {wall: [] for wall in OPPOSITE}
        for opening in [*room.doors, *room.windows]:
            _, _, lo, hi = opening_span(room, opening)
            _, _, start, end = wall_span(room, opening.wall)
            if lo < start + JAMB - EPS or hi > end - JAMB + EPS:
                issues.append(ValidationIssue(room_id=room.id, message=f"{room.name}: opening extends beyond wall/jamb clearance."))
            for old_lo, old_hi in intervals[opening.wall]:
                if min(hi, old_hi) > max(lo, old_lo) + EPS:
                    issues.append(ValidationIssue(room_id=room.id, message=f"{room.name}: wall openings overlap."))
            intervals[opening.wall].append((lo, hi))
        for window in room.windows:
            if not exterior(layout, room, window.wall):
                issues.append(ValidationIssue(room_id=room.id, message=f"{room.name}: window must be on an exterior wall."))
        for door in room.doors:
            if door.connects_to == "outside":
                if exterior(layout, room, door.wall) and room.floor == 0 and door.kind == "door":
                    entrances.add(room.id)
                elif room.floor != 0:
                    issues.append(ValidationIssue(room_id=room.id, message="Upper-floor exterior doors need a modeled landing; unsupported."))
                continue
            target = by_id.get(door.connects_to)
            if target is None:
                continue
            span = shared_span(room, door.wall, target)
            _, _, lo, hi = opening_span(room, door)
            if span is None or lo < span[0] + JAMB - EPS or hi > span[1] - JAMB + EPS:
                issues.append(ValidationIssue(room_id=room.id, message=f"{room.name}: door does not fit the shared wall with {target.name}."))
                continue
            matched = any(d.connects_to == room.id and d.wall == OPPOSITE[door.wall]
                          and d.kind == door.kind
                          and abs(opening_span(target, d)[2] - lo) <= EPS
                          and abs(opening_span(target, d)[3] - hi) <= EPS for d in target.doors)
            if not matched:
                issues.append(ValidationIssue(room_id=room.id, message=f"{room.name}: opening to {target.name} is not aligned on both sides."))
            else:
                graph[room.id].add(target.id)
    reached = set(entrances)
    todo = list(entrances)
    while todo:
        for neighbor in graph[todo.pop()] - reached:
            reached.add(neighbor)
            todo.append(neighbor)
    if not entrances:
        issues.append(ValidationIssue(message="A ground-floor exterior entrance is required."))
    for room in layout.rooms:
        if room.id not in reached:
            issues.append(ValidationIssue(room_id=room.id, message=f"{room.name} has no continuous path from the entrance."))
    return issues


def normalized_room_name(name: str) -> str:
    text = name.lower().replace("puja", "pooja").replace("prayer", "pooja")
    text = text.replace("restroom", "bathroom").replace("washroom", "bathroom").replace("toilet", "bathroom")
    text = text.replace("car park", "parking").replace("garage", "parking")
    text = re.sub(r"\b(?:dining hall|dinner room|dinner hall)\b", "dining room", text)
    text = re.sub(r"\bliving hall\b", "living room", text)
    return " ".join(re.findall(r"[a-z]+", text))


def validate_requirements(layout: Layout, spec: HouseSpec) -> list[ValidationIssue]:
    issues = []
    def report(message):
        issues.append(ValidationIssue(message=message))
    if layout.plot_width_ft != spec.plot_width_ft or layout.plot_length_ft != spec.plot_length_ft:
        report("Layout plot dimensions must match the house specification.")
    for bathroom in (r for r in layout.rooms if room_category(r.name) == "bathroom"):
        bedrooms = [r.width*r.length for r in layout.rooms
                    if r.floor == bathroom.floor and room_category(r.name) == "bedroom"]
        cap = min(spec.max_bathroom_area_sqft,
                  min(bedrooms)*spec.max_bathroom_bedroom_ratio if bedrooms else spec.max_bathroom_area_sqft)
        area = bathroom.width * bathroom.length
        if area > cap + EPS:
            report(f"{bathroom.name} is oversized at {area:g} sq ft; the configured limit is {cap:g} sq ft. "
                   "Shrink the bathroom and give the recovered area to bedrooms or living space. "
                   "Do not divide rooms into equal areas; preserve complete tiling and connected circulation.")
    for category, expected in (("bedroom", spec.num_bedrooms), ("bathroom", spec.num_bathrooms)):
        actual = sum(room_category(r.name) == category for r in layout.rooms)
        if expected is not None and actual != expected:
            report(f"Expected {expected} {category}(s), found {actual}.")
    if spec.attached_bathroom_bedrooms is not None:
        expected_targets = []
        for number in spec.attached_bathroom_bedrooms:
            bedroom = next((r for r in layout.rooms if r.name == f"Bedroom {number}"), None)
            if bedroom is None:
                report(f"Missing bedroom for attached bathroom: Bedroom {number}.")
                continue
            expected_targets.append(bedroom.id)
            bathrooms = [r for r in layout.rooms if room_category(r.name) == "bathroom"
                         and r.ensuite_for == bedroom.id]
            if len(bathrooms) != 1 or any(
                    [d.connects_to for d in bath.doors] != [bedroom.id] for bath in bathrooms):
                report(f"Bedroom {number} must have one attached bathroom with private direct access.")
        if sorted(r.ensuite_for for r in layout.rooms if r.ensuite_for) != sorted(expected_targets):
            report("Attached bathroom assignments must match the house specification.")
    if {r.floor for r in layout.rooms} != set(range(spec.num_floors or 1)):
        report(f"Expected floors 0 through {(spec.num_floors or 1) - 1}.")
    names = [normalized_room_name(r.name) for r in layout.rooms]
    # A must-have room describing the bedroom/bathroom split (e.g. "an
    # attached bathroom and a common bathroom") is already covered by the
    # num_bedrooms/num_bathrooms count check above, not a distinct named
    # room to require - the actual rooms may just be "Bathroom 1", etc.
    named_extras = [r for r in spec.must_have_rooms if room_category(r) not in ("bedroom", "bathroom")]
    for required in ["living", "dining", "kitchen", *named_extras]:
        tokens = set(normalized_room_name(required).split()) - {"room", "rooms", "area", "a", "the"}
        if tokens and not any(tokens <= set(name.split()) for name in names):
            report(f"Missing required space: {required}.")
    return issues


def validate_layout(layout: Layout, spec: HouseSpec | None = None) -> list[ValidationIssue]:
    issues = validate_geometry(layout) + validate_doors(layout)
    if spec is not None:
        issues += validate_requirements(layout, spec)
        facing = (spec.entrance_facing or "").lower().strip()
        if facing in OPPOSITE and not any(r.floor == 0 and d.connects_to == "outside" and d.wall == facing
                                        for r in layout.rooms for d in r.doors):
            issues.append(ValidationIssue(message=f"Entrance must face {facing}."))
    return issues
