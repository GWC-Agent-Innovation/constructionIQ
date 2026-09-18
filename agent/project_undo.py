"""Restore a saved revision without asking the LLM to recreate it."""
import re

from agent.pipeline import generate_and_save
from agent.schemas import HouseSpec, SessionState
from agent.validator import room_category, normalized_room_name, validate_layout


def is_undo_request(text: str) -> bool:
    return bool(re.fullmatch(
        r'(?:please\s+)?(?:undo|revoke|revert|roll\s*back)(?:\s+(?:the\s+)?(?:last\s+)?(?:change|changes|edit))?[.!]?',
        text.strip(), re.I))


def _legacy_spec(session: SessionState, layout) -> HouseSpec:
    """Old revisions lack requirement snapshots; recover structural facts only."""
    rooms = layout.rooms
    bedroom_numbers = {r.id: int(match[1]) for r in rooms
                       if (match := re.fullmatch(r'Bedroom (\d+)', r.name))}
    extras = [r.name for r in rooms if room_category(r.name) == 'parking' or (
        room_category(r.name) == 'private' and normalized_room_name(r.name) != 'kitchen')]
    restored = session.spec.model_copy(deep=True)
    restored.style_notes = None
    restored.plot_width_ft = layout.plot_width_ft
    restored.plot_length_ft = layout.plot_length_ft
    restored.num_bedrooms = len(bedroom_numbers)
    restored.num_bathrooms = sum(room_category(r.name) == 'bathroom' for r in rooms)
    restored.num_floors = len({r.floor for r in rooms})
    restored.attached_bathroom_bedrooms = sorted(bedroom_numbers[r.ensuite_for] for r in rooms
                                                if r.ensuite_for in bedroom_numbers)
    restored.include_dining_room = any('dining' in normalized_room_name(r.name) for r in rooms)
    restored.must_have_rooms = extras
    entrances = [door.wall for room in rooms if room_category(room.name) == 'public'
                 for door in room.doors if door.connects_to == 'outside' and door.kind == 'door']
    restored.entrance_facing = entrances[0] if entrances else None
    # Do not apply new constraints to a revision that predates them.
    restored.required_adjacencies = []
    for relation in session.spec.required_adjacencies:
        trial = restored.model_copy(deep=True)
        trial.required_adjacencies = [relation]
        if not any('must be next to' in issue.message for issue in validate_layout(layout, trial)):
            restored.required_adjacencies.append(relation)
    return restored


def undo_last_edit(session: SessionState):
    if session.layout is None:
        return None, ['There is no saved design to undo.']
    earlier = [r.version for r in session.layout_history if r.version < session.layout.version]
    target = session.revision_parents.get(session.layout.version, max(earlier, default=None))
    previous = next((r for r in session.layout_history if r.version == target), None)
    if previous is None:
        return None, ['There is no earlier saved design to restore.']
    original_spec = session.spec.model_copy(deep=True)
    original_options = session.layout_options
    session.spec = (session.revision_specs[target].model_copy(deep=True)
                    if target in session.revision_specs else _legacy_spec(session, previous))
    session.layout_options = []
    try:
        output, issues = generate_and_save(session, require_3d_success=True,
                                           selected_layout=previous, restore_revision=target)
    except Exception:
        session.spec, session.layout_options = original_spec, original_options
        raise
    if output is None:
        session.spec, session.layout_options = original_spec, original_options
    return output, issues
