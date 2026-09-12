"""Build physically aligned openings and windows from finalized room geometry."""
from agent.schemas import Door, HouseSpec, Layout, Room
from agent.openings import JAMB, WALLS, add_windows, connect, exterior, shared_span, wall_span
from agent.validator import room_category


def plan_openings(layout: Layout, spec: HouseSpec) -> Layout:
    result = layout.model_copy(deep=True)
    for room in result.rooms:
        room.doors = []
        room.windows = []
    public = [r for r in result.rooms if room_category(r.name) == "public"]
    private = [r for r in result.rooms if room_category(r.name) != "public"]

    def neighbors(a: Room, candidates: list[Room]):
        choices = []
        for b in candidates:
            if a.id == b.id:
                continue
            for wall in WALLS:
                span = shared_span(a, wall, b)
                if span and span[1] - span[0] >= 3.6:
                    choices.append((span[1] - span[0], b.id, wall, b))
        return sorted(choices, key=lambda item: (-item[0], item[1], item[2]))

    # Wide openings join rectangular public zones into connected open areas.
    linked = set()
    for room in public:
        for _, _, wall, target in neighbors(room, public):
            pair = tuple(sorted((room.id, target.id)))
            if pair not in linked:
                connect(room, wall, target, kind="opening")
                linked.add(pair)
    for room in private:
        choices = neighbors(room, public)
        if room.ensuite_for:
            choices = neighbors(room, [r for r in private if r.id == room.ensuite_for])
            if not choices:
                raise ValueError(f"{room.name} must connect directly to its assigned bedroom.")
        elif room_category(room.name) == "bathroom" and any(k in room.name.lower() for k in ("master", "attached", "bedroom")):
            bedrooms = neighbors(room, [r for r in private if room_category(r.name) == "bedroom"])
            if bedrooms:
                choices = bedrooms
        if not choices and room_category(room.name) == "bathroom":
            choices = neighbors(room, [r for r in private if room_category(r.name) == "bedroom"])
        if not choices:
            raise ValueError(f"{room.name} needs a shared wall at least 3.6 ft long with a public space"
                             + (" or bedroom." if room_category(room.name) == "bathroom" else "."))
        _, _, wall, target = choices[0]
        # Offset doors toward the end of the shared wall to keep the center of
        # small rooms usable and leave room for readable dimension labels.
        connect(room, wall, target, near_start=True)

    # Parking needs its own driveway door straight to the outside â€” a car
    # can't be driven in through the living room, so an interior connection
    # to a public room (added above, for people on foot) isn't enough on its own.
    for room in result.rooms:
        if room_category(room.name) != "parking":
            continue
        ext_walls = []
        for wall in WALLS:
            if exterior(result, room, wall):
                _, _, start, end = wall_span(room, wall)
                ext_walls.append((end - start, wall))
        if not ext_walls:
            raise ValueError(f"{room.name} needs to touch the plot boundary for a direct driveway door.")
        ext_walls.sort(reverse=True)
        _, wall = ext_walls[0]
        _, _, start, end = wall_span(room, wall)
        span_len = end - start
        width = min(8.0, span_len - 2 * JAMB)
        if width < 2.8:
            raise ValueError(f"{room.name}'s exterior wall is too short for a driveway door.")
        center = (start + end) / 2
        room.doors.append(Door(wall=wall, position=(center - start) / span_len, connects_to="outside", width=width))

    facing = (spec.entrance_facing or "").strip().lower()
    if facing and facing not in WALLS:
        raise ValueError("Entrance direction must be north, south, east or west.")
    entrance_options = [(r, wall) for r in public if r.floor == 0
                        for wall in ([facing] if facing else ["south", "north", "east", "west"])
                        if exterior(result, r, wall)]
    entrance_options.sort(key=lambda option: ("living" not in option[0].name.lower(), option[0].id))
    if not entrance_options:
        raise ValueError(f"A living/dining/hall space must touch the {facing or 'exterior'} boundary for the entrance.")
    room, wall = entrance_options[0]
    room.doors.append(Door(wall=wall, connects_to="outside", width=3.0))
    add_windows(result)
    return result
