"""Deterministic room-area planning and rectangular tiling.

Replaces LLM-generated coordinates entirely. The LLM (via DesignHints, see
agent/layout_agent.py) only ever influences relative room sizes and column
ordering; every coordinate here is computed, never guessed.

The construction is exact by design: a single full-width "Hallway" band
always separates the private band (bedrooms + attached bathrooms, optionally
parking) from the public band (living/dining/kitchen/common bathrooms/other
must-haves). Each ensuite bathroom gets a strip beside its assigned bedroom.
For deeper bedroom bands the bathroom sits above its bedroom instead, keeping
its area and proportions within the configured limits. Bedrooms reach the
full-width Hallway, and their bathrooms connect privately through them.
The Hallway is itself a "public"-category room
(agent.validator.room_category matches "hall"). That one hub relationship
gives, by construction: exact full-plot tiling (bands sum to the plot
dimensions), zero overlaps (grid partition), full public-space connectivity
(every public room touches the hub), and every private room's required
adjacency to a public/bedroom room - before doors are even planned. Column
*order* within a band is therefore never load-bearing for correctness; it's
only a best-effort quality knob driven by DesignHints.adjacency_preferences.
"""
from __future__ import annotations

from agent.door_planner import plan_openings
from agent.schemas import DesignHints, HouseSpec, Layout, Room
from agent.validator import (MAX_ASPECT_RATIO, MIN_ROOM_DIMENSION_FT,
                             normalized_room_name, room_category, validate_layout)

HALLWAY_DEPTH_FT = 5.0
MIN_SLOT_FT = 8.0
BED_DEPTH_OPTIONS = (14.0, 16.0, 12.0, 18.0, 20.0, 11.0, 22.0, 10.0, 24.0)
KITCHEN_STACK_WIDTH_OPTIONS = (10.0, 11.0, 12.0, 9.0, 13.0, 8.0, 14.0, 16.0)
CAR_PARKING_TARGET_SQFT = 190.0
BIKE_PARKING_TARGET_SQFT = 30.0
BIKE_PARKING_KEYWORDS = ("bike", "cycle", "scooter", "two wheeler", "two-wheeler", "motorcycle", "moped")


def _parking_target_sqft(name: str) -> float:
    lowered = name.lower()
    if any(keyword in lowered for keyword in BIKE_PARKING_KEYWORDS):
        return BIKE_PARKING_TARGET_SQFT
    return CAR_PARKING_TARGET_SQFT


def _base_and_extra_names(spec: HouseSpec) -> tuple[list[str], list[str]]:
    base = ["Living Room", "Dining Room", "Kitchen"]
    extras = []
    for name in spec.must_have_rooms:
        tokens = set(normalized_room_name(name).split()) - {"room", "rooms", "area", "a", "the"}
        if not any(tokens <= set(normalized_room_name(n).split()) for n in base):
            extras.append(name)
    return base, extras


# Real-world livable minimums used only to EXPLAIN an infeasible request (how
# far short the plot is) - never to size the actual layout. solve_layout's own
# constants above are the source of truth for what gets built.
BEDROOM_MIN_LIVABLE_SQFT = 90.0
BATHROOM_MIN_LIVABLE_SQFT = 35.0
KITCHEN_MIN_LIVABLE_SQFT = 70.0
LIVING_MIN_LIVABLE_SQFT = 100.0
DINING_MIN_LIVABLE_SQFT = 70.0
EXTRA_ROOM_MIN_LIVABLE_SQFT = 45.0
CIRCULATION_OVERHEAD_FRACTION = 0.15


def _split_extras(spec: HouseSpec) -> tuple[list[str], list[str]]:
    """Splits must-have extras into (parking_names, other_extras), dropping
    any that duplicate a structured count instead of naming a distinct room -
    e.g. a user describing "an attached bathroom and a common bathroom" is
    describing how num_bathrooms should split, already handled by
    attached_count/common_count, not a request for two MORE bathrooms.
    Letting one through as a generic extra would double-count against
    validate_requirements's bathroom/bedroom totals and reject every
    candidate for a room-count mismatch that isn't really there."""
    _, extras = _base_and_extra_names(spec)
    parking_names = [n for n in extras if room_category(n) == "parking"]
    other_extras = [n for n in extras if room_category(n) not in ("parking", "bathroom", "bedroom")]
    return parking_names, other_extras


def estimate_min_area_sqft(spec: HouseSpec) -> float:
    """A rough lower bound for what this room list needs in the real world -
    not the bare geometric floor (MIN_ROOM_DIMENSION_FT alone would call a
    5x5 ft "bedroom" fine, which nobody would actually accept)."""
    bedrooms = spec.num_bedrooms or 0
    bathrooms = spec.num_bathrooms or 0
    parking_names, other_extras = _split_extras(spec)

    total = (
        bedrooms * BEDROOM_MIN_LIVABLE_SQFT
        + bathrooms * BATHROOM_MIN_LIVABLE_SQFT
        + KITCHEN_MIN_LIVABLE_SQFT
        + LIVING_MIN_LIVABLE_SQFT
        + DINING_MIN_LIVABLE_SQFT
        + len(other_extras) * EXTRA_ROOM_MIN_LIVABLE_SQFT
        + sum(_parking_target_sqft(name) for name in parking_names)
    )
    return total * (1 + CIRCULATION_OVERHEAD_FRACTION)


def _weight_for(name: str, hints: DesignHints, default: float) -> float:
    key = normalized_room_name(name)
    for hint_name, multiplier in hints.room_weights.items():
        hint_key = normalized_room_name(hint_name)
        if hint_key and (hint_key == key or hint_key in key or key in hint_key):
            try:
                return default * max(0.5, min(2.0, float(multiplier)))
            except (TypeError, ValueError):
                return default
    return default


def _order_by_adjacency(names: list[str], hints: DesignHints) -> list[str]:
    """Best-effort reorder so hinted pairs land next to each other. Never
    required for correctness - a failed reorder just leaves the input order."""
    ordered = list(names)
    keys = {n: normalized_room_name(n) for n in ordered}
    for pref in hints.adjacency_preferences:
        a_key, b_key = normalized_room_name(pref.a), normalized_room_name(pref.b)
        a = next((n for n in ordered if a_key and a_key in keys[n]), None)
        b = next((n for n in ordered if b_key and b_key in keys[n]), None)
        if a is None or b is None or a == b:
            continue
        ai, bi = ordered.index(a), ordered.index(b)
        if abs(ai - bi) == 1:
            continue
        ordered.remove(b)
        ordered.insert(ordered.index(a) + 1, b)
    return ordered


def _clamped_partition(weights: list[float], total: float, min_size: float) -> list[float] | None:
    """Split `total` into len(weights) shares proportional to weight, each
    at least min_size. None means that's not achievable with this total -
    the caller should try other parameters, not force an invalid layout."""
    n = len(weights)
    if n == 0 or n * min_size > total + 1e-6:
        return None
    weights = [max(w, 1e-6) for w in weights]
    sizes = [total * w / sum(weights) for w in weights]
    for _ in range(n + 1):
        under = [i for i, s in enumerate(sizes) if s < min_size - 1e-9]
        if not under:
            break
        deficit = sum(min_size - sizes[i] for i in under)
        for i in under:
            sizes[i] = min_size
        free = [i for i in range(n) if i not in under]
        free_weight = sum(weights[i] for i in free)
        if not free or free_weight <= 0:
            return None
        for i in free:
            sizes[i] -= deficit * weights[i] / free_weight
    sizes[-1] += total - sum(sizes)  # soak up float residual so the split is exact
    if sizes[-1] < min_size - 1e-6:
        return None
    return sizes


def _fits_aspect(width: float, length: float) -> bool:
    longer, shorter = max(width, length), min(width, length)
    return shorter > 0 and longer / shorter <= MAX_ASPECT_RATIO


def _private_band(spec: HouseSpec, hints: DesignHints, width: float, depth: float,
                  y: float, attached_count: int, parking_name: str | None) -> list[Room] | None:
    """Tile parking and bedroom suites; each ensuite belongs to one bedroom."""
    x = 0.0
    rooms: list[Room] = []
    if parking_name:
        target = _parking_target_sqft(parking_name)
        min_w = max(MIN_ROOM_DIMENSION_FT, depth / MAX_ASPECT_RATIO)
        max_w = width - MIN_SLOT_FT
        if min_w > max_w:
            return None
        park_w = min(max_w, max(min_w, target / depth))
        if not _fits_aspect(park_w, depth):
            return None
        rooms.append(Room(id="parking", name=parking_name, x=x, y=y, width=park_w, length=depth))
        x += park_w
    assignments = spec.attached_bathroom_bedrooms
    if assignments is None:
        assignments = list(range(1, attached_count + 1))
    bath_width = max(MIN_ROOM_DIMENSION_FT, depth / MAX_ASPECT_RATIO)
    stack_bath = bath_width * depth > spec.max_bathroom_area_sqft
    # Each bathroom gets a strip beside its own bedroom, instead of stacking
    # every attached bathroom against the first bedroom in the house.
    available_width = width - x - (0 if stack_bath else bath_width * len(assignments))
    bed_rooms = _bedroom_band(spec, hints, available_width, depth, y, x)
    if bed_rooms is None:
        return None
    offset = 0.0
    for bedroom in bed_rooms:
        bedroom.x += offset
        number = int(bedroom.name.rsplit(" ", 1)[1])
        if number in assignments:
            if stack_bath:
                bath_depth = max(MIN_ROOM_DIMENSION_FT, bedroom.width / MAX_ASPECT_RATIO)
                if bath_depth * bedroom.width > spec.max_bathroom_area_sqft:
                    # A wide suite must not force its bathroom to span the
                    # entire width. Put a compact bath at the hallway end,
                    # with a connected hallway alcove filling the rest of
                    # that strip. The bedroom stays rectangular and the
                    # bathroom still opens exclusively into that bedroom.
                    compact_width = 8.0
                    compact_depth = max(MIN_ROOM_DIMENSION_FT,
                                        min(min(55.0, spec.max_bathroom_area_sqft) / compact_width,
                                            depth - bedroom.width / MAX_ASPECT_RATIO - 1e-6))
                    remaining_depth = depth - compact_depth
                    if (compact_width * compact_depth > spec.max_bathroom_area_sqft
                            or bedroom.width - compact_width < MIN_ROOM_DIMENSION_FT
                            or remaining_depth < MIN_ROOM_DIMENSION_FT
                            or not _fits_aspect(compact_width, compact_depth)
                            or not _fits_aspect(bedroom.width, remaining_depth)):
                        return None
                    bedroom.length = remaining_depth
                    rooms.append(Room(id=f"attbath_{number - 1}", name=f"Attached Bathroom {number}",
                                      x=bedroom.x, y=y + remaining_depth,
                                      width=compact_width, length=compact_depth, ensuite_for=bedroom.id))
                    rooms.append(Room(id=f"suite_hall_{number}", name=f"Hallway Alcove {number}",
                                      x=bedroom.x + compact_width, y=y + remaining_depth,
                                      width=bedroom.width - compact_width, length=compact_depth))
                    rooms.append(bedroom)
                    continue
                if (depth - bath_depth < MIN_ROOM_DIMENSION_FT
                        or not _fits_aspect(bedroom.width, depth - bath_depth)):
                    return None
                rooms.append(Room(id=f"attbath_{number - 1}", name=f"Attached Bathroom {number}",
                                  x=bedroom.x, y=y, width=bedroom.width, length=bath_depth,
                                  ensuite_for=bedroom.id))
                bedroom.y += bath_depth
                bedroom.length -= bath_depth
                rooms.append(bedroom)
                continue
            rooms.append(Room(id=f"attbath_{number - 1}", name=f"Attached Bathroom {number}",
                              x=bedroom.x, y=y, width=bath_width, length=depth,
                              ensuite_for=bedroom.id))
            bedroom.x += bath_width
            offset += bath_width
        rooms.append(bedroom)
    return rooms


def _bedroom_band(spec: HouseSpec, hints: DesignHints, width: float, depth: float,
                  y: float, x_offset: float = 0.0) -> list[Room] | None:
    names = [f"Bedroom {i + 1}" for i in range(spec.num_bedrooms or 0)]
    names = _order_by_adjacency(names, hints)
    weights = [_weight_for(name, hints, 1.0) for name in names]
    slot_widths = _clamped_partition(weights, width, MIN_SLOT_FT)
    if slot_widths is None:
        return None
    rooms: list[Room] = []
    x = x_offset
    for i, (name, slot_w) in enumerate(zip(names, slot_widths)):
        if not _fits_aspect(slot_w, depth):
            return None
        rooms.append(Room(id=f"{re_id(name)}_{i}", name=name, x=x, y=y, width=slot_w, length=depth))
        x += slot_w
    return rooms


def _kitchen_bath_stack(width: float, depth: float, bathroom_count: int,
                        bath_target: float, x: float, y: float) -> list[Room] | None:
    """Kitchen shares a narrow column with all the bathrooms, each sized
    toward its area target (not a generic weight share) and the kitchen
    absorbing whatever depth is left. The column's own width is swept by the
    caller rather than derived from weights - a bathroom's width and depth
    are both small, independent of how much total width the public band
    happens to have. Every bathroom goes through here (see _private_band for
    why none are placed alongside bedrooms), so this sizing is independent
    of bed_depth by construction and can't hit the aspect-vs-area-cap
    conflict a bedroom-depth-spanning bathroom would."""
    bath_depths = []
    for _ in range(bathroom_count):
        d = max(MIN_ROOM_DIMENSION_FT, min(depth * 0.4, bath_target / width))
        if not _fits_aspect(width, d):
            return None
        bath_depths.append(d)
    kitchen_len = depth - sum(bath_depths)
    if kitchen_len < MIN_ROOM_DIMENSION_FT or not _fits_aspect(width, kitchen_len):
        return None
    rooms = [Room(id="kitchen", name="Kitchen", x=x, y=y, width=width, length=kitchen_len)]
    cy = y + kitchen_len
    for i, d in enumerate(bath_depths):
        name = "Bathroom" if bathroom_count == 1 else f"Bathroom {i + 1}"
        rooms.append(Room(id=f"bath_{i}", name=name, x=x, y=cy, width=width, length=d))
        cy += d
    return rooms


def _public_band(spec: HouseSpec, hints: DesignHints, width: float, depth: float, y: float,
                 bathroom_count: int, extra_names: list[str]) -> list[Room] | None:
    """Column order: Living, Dining, [Kitchen+Bath stack], then extras.

    The stack MUST sit right after Dining, not after the extras: a stacked
    bathroom only touches the Hallway through its neighbor above (Kitchen),
    not directly, so it needs a lateral neighbor spanning its full depth that
    is itself public (Living/Dining) to satisfy the circulation validator.
    An "extra" room (private, single-row, full pub_depth) doesn't have that
    problem - it always touches the Hallway directly - so it's fine on
    either side, but must not be sandwiched between Dining and the stack.
    """
    extra_names = _order_by_adjacency(extra_names, hints)
    for kitchen_w in KITCHEN_STACK_WIDTH_OPTIONS:
        remaining_width = width - kitchen_w
        if remaining_width < MIN_SLOT_FT * 2:
            continue
        column_names = ["Living Room", "Dining Room", *extra_names]
        weights = [_weight_for("Living Room", hints, 1.2), _weight_for("Dining Room", hints, 1.0)]
        weights += [_weight_for(name, hints, 0.8) for name in extra_names]
        col_widths = _clamped_partition(weights, remaining_width, MIN_SLOT_FT)
        if col_widths is None or any(not _fits_aspect(w, depth) for w in col_widths):
            continue
        bath_target = min(55.0, spec.max_bathroom_area_sqft)
        stack_x = col_widths[0] + col_widths[1]
        stack = _kitchen_bath_stack(kitchen_w, depth, bathroom_count, bath_target, x=stack_x, y=y)
        if stack is None:
            continue
        rooms = []
        x = 0.0
        for i, (name, w) in enumerate(zip(column_names, col_widths)):
            if i == 2:
                x += kitchen_w  # stack occupies this slot; extras continue after it
            room_id = "living" if name == "Living Room" else "dining" if name == "Dining Room" else re_id(name)
            rooms.append(Room(id=f"{room_id}_{len(rooms)}", name=name, x=x, y=y, width=w, length=depth))
            x += w
        rooms.extend(stack)
        return rooms
    return None


def re_id(name: str) -> str:
    return normalized_room_name(name).replace(" ", "_") or "room"


def _snap_to_plot(layout: Layout) -> Layout:
    """Chained partitions/transposes/flips accumulate float noise (e.g. a
    room's far edge landing at 60.00000000000001 instead of 60) - harmless
    in principle, but validate_geometry's boundary check has no epsilon, so
    an overshoot of 1e-13 ft reads as "falls outside the plot". Clip it."""
    eps = 1e-6
    for room in layout.rooms:
        if -eps < room.x < 0:
            room.x = 0.0
        if -eps < room.y < 0:
            room.y = 0.0
        overshoot_x = room.x + room.width - layout.plot_width_ft
        if 0 < overshoot_x < eps:
            room.width -= overshoot_x
        overshoot_y = room.y + room.length - layout.plot_length_ft
        if 0 < overshoot_y < eps:
            room.length -= overshoot_y
    return layout


def _repair_gap(layout: Layout) -> Layout:
    """Defensive only: the banded construction tiles exactly by design, so
    this should be a no-op in practice. Kept as the "gap detection/auto
    repair" safety net in case a float residual ever slips through."""
    plot_area = layout.plot_width_ft * layout.plot_length_ft
    covered = sum(r.width * r.length for r in layout.rooms)
    gap = plot_area - covered
    if gap <= 1.0 or not layout.rooms:
        return layout
    last = layout.rooms[-1]
    if abs(last.x + last.width - layout.plot_width_ft) < 0.5 and last.width > 0:
        last.length += gap / last.width
    elif abs(last.y + last.length - layout.plot_length_ft) < 0.5 and last.length > 0:
        last.width += gap / last.length
    return layout


def solve_layout(spec: HouseSpec, hints: DesignHints | None = None) -> Layout | None:
    if not spec.is_ready_for_layout() or (spec.num_floors or 1) != 1:
        return None
    bedrooms, bathrooms = spec.num_bedrooms or 0, spec.num_bathrooms or 0
    if bedrooms < 1 or bathrooms < 1:
        return None
    hints = hints or DesignHints()

    parking_names, other_extras = _split_extras(spec)
    parking_name = parking_names[0] if parking_names else None

    # Retain the legacy default split for briefs without recorded assignments.
    # A soft hint can fall back; explicit saved assignments below cannot.
    default_attached = min(bathrooms - 1, bedrooms) if bathrooms > 1 else 0
    if default_attached == 1:
        default_attached = 0
    requested_attached = min(hints.attached_bathrooms, bathrooms, bedrooms)
    attached_candidates = list(dict.fromkeys(
        ([requested_attached] if requested_attached > 0 else []) + [default_attached]
    ))
    if spec.attached_bathroom_bedrooms is not None:
        assignments = spec.attached_bathroom_bedrooms
        if (len(set(assignments)) != len(assignments)
                or any(n < 1 or n > bedrooms for n in assignments)
                or len(assignments) > bathrooms):
            return None
        # Persisted user requirements are mandatory; never silently turn an
        # explicitly requested ensuite into a common bathroom.
        attached_candidates = [len(assignments)]

    # attached_count is the OUTERMOST loop, not nested inside bed_depth: the
    # sweep must fully exhaust every bed_depth/transpose/flip combination for
    # the user's PREFERRED arrangement before ever falling back to the
    # default, or it could settle for an easy default-split solution at the
    # very first bed_depth tried while a hinted arrangement was still
    # achievable at a different bed_depth further down the list.
    for attached_count in attached_candidates:
        common_count = bathrooms - attached_count
        for transpose in (False, True):
            plot_w, plot_l = ((spec.plot_length_ft, spec.plot_width_ft) if transpose
                              else (spec.plot_width_ft, spec.plot_length_ft))
            for bed_depth in BED_DEPTH_OPTIONS:
                pub_depth = plot_l - bed_depth - HALLWAY_DEPTH_FT
                if pub_depth < 10.0 or bed_depth < MIN_ROOM_DIMENSION_FT:
                    continue

                rooms: list[Room] = []
                private_rooms = _private_band(spec, hints, plot_w, bed_depth, 0.0, attached_count, parking_name)
                if private_rooms is None:
                    continue
                rooms.extend(private_rooms)
                rooms.append(Room(id="hall", name="Hallway", x=0.0, y=bed_depth, width=plot_w, length=HALLWAY_DEPTH_FT))
                pub_rooms = _public_band(spec, hints, plot_w, pub_depth, bed_depth + HALLWAY_DEPTH_FT,
                                         common_count, other_extras)
                if pub_rooms is None:
                    continue
                rooms.extend(pub_rooms)

                base_rooms = rooms
                if transpose:
                    base_rooms = [r.model_copy(update=dict(x=r.y, y=r.x, width=r.length, length=r.width))
                                 for r in base_rooms]
                for flip_x in (False, True):
                    for flip_y in (False, True):
                        candidate_rooms = base_rooms
                        if flip_x:
                            candidate_rooms = [r.model_copy(update={"x": spec.plot_width_ft - r.x - r.width})
                                               for r in candidate_rooms]
                        if flip_y:
                            candidate_rooms = [r.model_copy(update={"y": spec.plot_length_ft - r.y - r.length})
                                               for r in candidate_rooms]
                        candidate = Layout(plot_width_ft=spec.plot_width_ft, plot_length_ft=spec.plot_length_ft,
                                          rooms=[r.model_copy(deep=True) for r in candidate_rooms])
                        candidate = _snap_to_plot(candidate)
                        candidate = _repair_gap(candidate)
                        try:
                            wired = plan_openings(candidate, spec)
                        except ValueError:
                            continue
                        if not validate_layout(wired, spec):
                            wired.planning_notes = list(hints.planning_notes)[:6]
                            return wired
    return None
