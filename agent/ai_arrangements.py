"""Compile AI-selected rectangular partitions into exact, validated geometry.

AI chooses which rooms go together and how each group splits. Python owns
coordinates, room identities, openings and all acceptance checks.
"""
from agent.schemas import DesignHints, HouseSpec, Layout, LayoutProposal, Room
from agent.layout_solver import _split_extras, _snap_to_plot, _parking_target_sqft, re_id
from agent.door_planner import plan_openings
from agent.validator import room_category, validate_layout


MAX_HALLWAY_AREA_FRACTION = 0.15
MAX_HALLWAY_WIDTH_FT = 6.0


def _hall_entry(spec: HouseSpec, key: str) -> dict:
    return dict(id=key, name=f"Hallway {key[-1]}", minimum_area_sqft=25,
                maximum_area_sqft=spec.plot_width_ft * spec.plot_length_ft * MAX_HALLWAY_AREA_FRACTION,
                ensuite_for=None)


def room_catalog(spec: HouseSpec, hints: DesignHints | None = None) -> dict[str, dict]:
    assignments = spec.attached_bathroom_bedrooms
    if assignments is None:
        count = min((hints.attached_bathrooms if hints else 0), spec.num_bathrooms or 0,
                    spec.num_bedrooms or 0)
        assignments = list(range(1, count + 1))
    catalog = {}
    def add(key, name, minimum, ensuite_for=None):
        catalog[key] = dict(id=key, name=name, minimum_area_sqft=minimum, ensuite_for=ensuite_for)
        if room_category(name) == 'bathroom':
            catalog[key]['maximum_area_sqft'] = spec.max_bathroom_area_sqft
        if room_category(name) == 'parking':
            catalog[key]['maximum_area_sqft'] = spec.max_parking_area_sqft
    for i in range(spec.num_bedrooms or 0):
        add(f"bedroom_{i}", f"Bedroom {i + 1}", 90)
    for number in assignments:
        add(f"attbath_{number - 1}", f"Att. Bath {number}", 35, f"bedroom_{number - 1}")
    common = (spec.num_bathrooms or 0) - len(assignments)
    for i in range(common):
        add(f"bath_{i}", "Bathroom" if common == 1 else f"Bathroom {i + 1}", 35)
    base_rooms = [('living_0', 'Living Room', 100), ('kitchen', 'Kitchen', 70)]
    if spec.include_dining_room:
        base_rooms.insert(1, ('dining_1', 'Dining Room', 70))
    for key, name, area in base_rooms:
        add(key, name, area)
    parking, extras = _split_extras(spec)
    for i, name in enumerate(parking):
        add(f"parking_{i}" if len(parking) > 1 else "parking", name, _parking_target_sqft(name))
    for i, name in enumerate(extras, 2):
        add(f"{re_id(name)}_{i}", name, 45)
    return catalog


def compile_proposal(spec: HouseSpec, hints: DesignHints, proposal: LayoutProposal) -> tuple[Layout | None, list[str]]:
    if not spec.is_ready_for_layout() or (spec.num_floors or 1) != 1:
        return None, ["A complete single-floor specification is required."]
    catalog = room_catalog(spec, hints)
    nodes = {node.id: node for node in proposal.nodes}
    if len(nodes) != len(proposal.nodes):
        return None, ["Node IDs must be unique."]
    visited, used, rooms = set(), set(), []
    def split(key, x, y, width, length):
        if key not in nodes or key in visited:
            raise ValueError(f"Missing, repeated or cyclic node: {key}.")
        visited.add(key)
        node = nodes[key]
        if node.room_key is not None:
            if any(value is not None for value in (node.axis, node.fraction, node.first, node.second)):
                raise ValueError(f"Leaf {key} must only name a room.")
            room_key = node.room_key
            if room_key in used:
                raise ValueError(f"Room {room_key} occurs more than once.")
            used.add(room_key)
            entry = catalog.get(room_key)
            if entry is None and room_key in ('hall_1', 'hall_2', 'hall_3', 'hall_4'):
                entry = _hall_entry(spec, room_key)
            if entry is None:
                raise ValueError(f"Unknown room: {room_key}. Use the supplied room catalog.")
            if width * length + 1e-6 < entry['minimum_area_sqft']:
                raise ValueError(f"{room_key} needs at least {entry['minimum_area_sqft']} sq ft; proposal provides {width * length:.1f}.")
            rooms.append(Room(id=entry['id'], name=entry['name'], x=x, y=y, width=width,
                              length=length, ensuite_for=entry['ensuite_for']))
        else:
            if node.axis is None or node.fraction is None or node.first is None or node.second is None:
                raise ValueError(f"Split {key} needs axis, fraction, first and second.")
            if node.axis == 'x':
                first_width = width * node.fraction
                split(node.first, x, y, first_width, length)
                split(node.second, x + first_width, y, width - first_width, length)
            else:
                first_length = length * node.fraction
                split(node.first, x, y, width, first_length)
                split(node.second, x, y + first_length, width, length - first_length)
    try:
        split(proposal.root, 0.0, 0.0, spec.plot_width_ft, spec.plot_length_ft)
        if len(visited) != len(nodes):
            raise ValueError("Remove unused nodes from the proposal.")
        missing = set(catalog) - used
        if missing:
            raise ValueError("Missing required rooms: " + ', '.join(sorted(missing)))
        layout = _snap_to_plot(Layout(plot_width_ft=spec.plot_width_ft, plot_length_ft=spec.plot_length_ft,
                                     rooms=rooms, design_name=proposal.name, planning_source='ai_arrangement'))
        halls = [room for room in rooms if room.id.startswith('hall_')]
        hall_area = sum(room.width * room.length for room in halls)
        plot_area = spec.plot_width_ft * spec.plot_length_ft
        if hall_area > plot_area * MAX_HALLWAY_AREA_FRACTION + 1e-6:
            raise ValueError(f"Hallways use {100 * hall_area / plot_area:.1f}% of the home; maximum is 15%. "
                             "Regroup rooms and give this area to usable living spaces.")
        if any(min(room.width, room.length) > MAX_HALLWAY_WIDTH_FT + 1e-6 for room in halls):
            raise ValueError("Hallways must have a short side of 5-6 ft; do not use a large room as circulation.")
        layout = plan_openings(layout, spec)
        issues = validate_layout(layout, spec)
        # Common bathrooms must remain accessible from a public zone.
        by_id = {room.id: room for room in layout.rooms}
        for room in rooms:
            if room_category(room.name) == 'bathroom' and not room.ensuite_for:
                actual = by_id[room.id]
                if not any(d.connects_to in by_id and room_category(by_id[d.connects_to].name) == 'public' for d in actual.doors):
                    raise ValueError(f"{room.id} needs direct common access from living, dining or a hallway.")
        if issues:
            return None, [issue.message for issue in issues][:8]
        layout.planning_notes = [f"AI arrangement: {proposal.name}.",
                                 "Room dimensions, openings and access checked by the layout validator.",
                                 f"Dedicated hallways occupy {100 * hall_area / plot_area:.1f}% of floor area."]
        return layout, []
    except (ValueError, RecursionError) as exc:
        return None, [str(exc)]


def valid_proposals(spec: HouseSpec, hints: DesignHints) -> tuple[list[Layout], list[str]]:
    valid, errors, seen = [], [], set()
    for proposal in hints.layout_proposals:
        layout, issues = compile_proposal(spec, hints, proposal)
        if layout is None:
            layout = fit_proposal(spec, hints, proposal)
        if layout is None:
            errors.extend(f"{proposal.name}: {issue}" for issue in issues)
            continue
        signature = tuple(sorted((r.id, round(r.x, 4), round(r.y, 4), round(r.width, 4), round(r.length, 4)) for r in layout.rooms))
        if signature not in seen:
            if any(other.design_name == layout.design_name for other in valid):
                layout.design_name = f"{layout.design_name[:70]} ({len(valid) + 1})"
            valid.append(layout)
            seen.add(signature)
    # Prefer valid proposals that devote less of the home to circulation.
    # Stable sorting preserves the AI preference order when usage is equal.
    valid.sort(key=lambda layout: sum(r.width * r.length for r in layout.rooms if r.id.startswith('hall_')))
    return valid, errors[:12]


def fit_proposal(spec: HouseSpec, hints: DesignHints, proposal: LayoutProposal) -> Layout | None:
    """Bounded size search: preserve AI room grouping and split directions.

    Fractions are preferences. Derive feasible cut ranges from each child's
    minimum area, bathroom cap and aspect ratio, then explore up to 5,000
    subtree placements / 32 complete candidates. Never relax validation.
    """
    if not spec.is_ready_for_layout() or (spec.num_floors or 1) != 1:
        return None
    catalog = room_catalog(spec, hints)
    nodes = {node.id: node for node in proposal.nodes}
    seen, used, areas = set(), set(), {}
    if len(nodes) != len(proposal.nodes):
        return None
    def inspect(key):
        if key not in nodes or key in seen:
            raise ValueError('Invalid tree')
        seen.add(key)
        node = nodes[key]
        if node.room_key is not None:
            if any(v is not None for v in (node.axis, node.fraction, node.first, node.second)):
                raise ValueError('Mixed leaf and split')
            if node.room_key in used:
                raise ValueError('Repeated room')
            used.add(node.room_key)
            if node.room_key not in catalog:
                if node.room_key not in ('hall_1', 'hall_2', 'hall_3', 'hall_4'):
                    raise ValueError('Unknown room')
                catalog[node.room_key] = _hall_entry(spec, node.room_key)
            areas[key] = catalog[node.room_key]['minimum_area_sqft']
        else:
            if None in (node.axis, node.fraction, node.first, node.second):
                raise ValueError('Incomplete split')
            areas[key] = inspect(node.first) + inspect(node.second)
        return areas[key]
    try:
        required = set(catalog)
        inspect(proposal.root)
        if seen != set(nodes) or not required <= used:
            return None
    except ValueError:
        return None
    remaining = [5000]
    def bounds(key, other, total):
        node = nodes[key]
        lower, upper = max(5.0, areas[key] / other), total - 5.0
        if node.room_key is not None:
            entry = catalog[node.room_key]
            if not node.room_key.startswith('hall_'):
                lower, upper = max(lower, other / 2.2), min(upper, other * 2.2)
            elif other > MAX_HALLWAY_WIDTH_FT:
                upper = min(upper, MAX_HALLWAY_WIDTH_FT)
            if 'maximum_area_sqft' in entry:
                upper = min(upper, entry['maximum_area_sqft'] / other)
        return lower, upper
    def placements(key, width, length):
        remaining[0] -= 1
        if remaining[0] < 0 or width < 5 - 1e-8 or length < 5 - 1e-8 or width * length < areas[key] - 1e-8:
            return
        node = nodes[key]
        if node.room_key is not None:
            entry = catalog[node.room_key]
            if node.room_key.startswith('hall_') and min(width, length) > MAX_HALLWAY_WIDTH_FT + 1e-8:
                return
            if not node.room_key.startswith('hall_') and max(width, length) / min(width, length) > 2.2 + 1e-8:
                return
            if width * length > entry.get('maximum_area_sqft', float('inf')) + 1e-8:
                return
            yield {}
            return
        total, other = (width, length) if node.axis == 'x' else (length, width)
        lo_a, hi_a = bounds(node.first, other, total)
        lo_b, hi_b = bounds(node.second, other, total)
        low = max(.05, lo_a / total, 1 - hi_b / total)
        high = min(.95, hi_a / total, 1 - lo_b / total)
        if low > high + 1e-9:
            return
        values = [node.fraction, min(high, max(low, node.fraction)),
                  (low + high) / 2, areas[node.first] / areas[key], low, high,
                  *(i / 20 for i in range(1, 20))]
        fractions = list(dict.fromkeys(v for v in values if low - 1e-9 <= v <= high + 1e-9))
        for fraction in fractions:
            if remaining[0] < 0:
                return
            if node.axis == 'x':
                first_size, second_size = (width * fraction, length), (width * (1 - fraction), length)
            else:
                first_size, second_size = (width, length * fraction), (width, length * (1 - fraction))
            for first in placements(node.first, *first_size):
                for second in placements(node.second, *second_size):
                    yield {key: fraction, **first, **second}
    for index, fractions in enumerate(placements(proposal.root, spec.plot_width_ft, spec.plot_length_ft)):
        if index >= 32:
            break
        adjusted = proposal.model_copy(deep=True)
        for node in adjusted.nodes:
            if node.id in fractions:
                node.fraction = fractions[node.id]
        layout, _ = compile_proposal(spec, hints, adjusted)
        if layout is not None:
            layout.planning_notes.append('Python adjusted room proportions while retaining the AI room arrangement.')
            return layout
    return None
