"""Lightweight LLM design judgment, followed by deterministic room placement.

The LLM no longer emits geometry. It contributes DesignHints (relative room
sizes, adjacency preferences, a short user-facing rationale); agent.layout_solver
turns those into an exact, validated Layout. See agent/layout_solver.py for why
this split is reliable: the LLM is asked for the one thing it's actually good
at (design judgment), and code owns the one thing it was unreliable at (exact
non-overlapping coordinates).
"""
from __future__ import annotations
import json
import os
from pydantic import ValidationError
from agent.llm_client import chat
from agent.layout_solver import estimate_min_area_sqft, solve_layout
from agent.schemas import DesignHints, HouseSpec, Layout

MAX_HINT_ROUNDS = 2

HINTS_SYSTEM_PROMPT = """You bring design judgment to a residential floor plan; a
deterministic solver owns every coordinate, wall, door and window, so never
describe geometry, positions, or dimensions in feet.

Call plan_design_hints with:
- room_weights: relative size emphasis for rooms the user actually cares about
  (e.g. "spacious living room" -> {"living room": 1.3}). Only include rooms you
  have a real opinion on; omit the rest. Roughly 0.6 (compact) to 1.6 (spacious).
- adjacency_preferences: soft pairs you'd like kept near each other (e.g. kitchen
  next to dining room, or a pooja room away from bathrooms). Best-effort only.
- attached_bathrooms: how many bathrooms should be attached/ensuite to a bedroom
  (private, opening directly off a bedroom) rather than a shared bathroom off
  the hallway/kitchen area. Set this only when the user explicitly asks for an
  attached/ensuite bathroom, or one "inside"/"in" a bedroom - never invent this
  on your own. The solver honors it when the plot allows; if it truly can't fit
  one, it falls back to a shared bathroom, so don't worry about feasibility here.
- planning_notes: 3-6 short, factual, user-facing notes covering privacy zoning,
  the entrance route, kitchen-dining relationship, ventilation, and any
  trade-off the plot size forces (e.g. "car parking takes space off the
  buildable area"). These are shown to the user, not hidden reasoning.

Respect the requested style notes and entrance direction. Do not invent room
requirements the user did not ask for. Always call plan_design_hints - never
reply with plain text instead."""

PLAN_HINTS_TOOL = {
    "type": "function",
    "function": {
        "name": "plan_design_hints",
        "description": "Record design judgment for the deterministic layout solver: relative "
                        "room-size emphasis, adjacency preferences, and a short user-facing "
                        "rationale. Never coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "room_weights": {
                    "type": "object",
                    "description": "Room name -> relative size multiplier (e.g. {\"living room\": 1.3}). "
                                    "Omit rooms you have no opinion on.",
                    "additionalProperties": {"type": "number"},
                },
                "adjacency_preferences": {
                    "type": "array",
                    "description": "Soft placement preferences, e.g. keep the kitchen next to the dining room.",
                    "items": {
                        "type": "object",
                        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                        "required": ["a", "b"],
                    },
                },
                "attached_bathrooms": {
                    "type": "integer", "minimum": 0,
                    "description": "How many bathrooms the user explicitly wants attached/ensuite to a "
                                    "bedroom. Only set above 0 if they actually asked for this.",
                },
                "planning_notes": {
                    "type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 6,
                    "description": "3-6 short, factual notes explaining the design rationale.",
                },
            },
            "required": ["planning_notes"],
        },
    },
}


def _normalize_adjacency_preferences(args: dict) -> None:
    """Despite the tool schema requiring {"a": ..., "b": ...} objects, the
    model sometimes emits plain 2-element pairs (e.g. ["kitchen", "dining
    room"]) instead - a common tool-schema-adherence slip. Left unhandled,
    that single field failing validation discards the ENTIRE hints payload,
    including fields (like attached_bathrooms) the model got exactly right."""
    prefs = args.get("adjacency_preferences")
    if not isinstance(prefs, list):
        return
    normalized = []
    for item in prefs:
        if isinstance(item, dict):
            normalized.append(item)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            normalized.append({"a": item[0], "b": item[1]})
        # Anything else (wrong shape) is dropped rather than failing the whole call.
    args["adjacency_preferences"] = normalized


def _get_design_hints(spec: HouseSpec, edit_request: str | None, design_context: str | None) -> DesignHints:
    """Never raises: a hint-call failure just means the solver uses neutral
    defaults instead of blocking generation entirely."""
    content = f"House specification: {spec.model_dump_json()}"
    if design_context:
        content += ("\nThe user's own words so far (the structured specification above already "
                    "controls dimensions and counts): " + design_context)
    if edit_request:
        content += f"\nRequested changes: {edit_request}"
    for attempt in range(MAX_HINT_ROUNDS):
        try:
            response = chat(messages=[{"role": "system", "content": HINTS_SYSTEM_PROMPT},
                                      {"role": "user", "content": content}], tools=[PLAN_HINTS_TOOL],
                            timeout_seconds=float(os.environ.get("OLLAMA_REQUEST_TIMEOUT_SECONDS", "90")),
                            options={"temperature": 0.2},
                            think=os.environ.get("OLLAMA_LAYOUT_THINK", "low"))
        except Exception as exc:
            print(f"  [hints] design-hints request failed, continuing with defaults: {exc}", flush=True)
            return DesignHints()
        tool_calls = response.get("message", {}).get("tool_calls") or []
        call = next((item.get("function", {}) for item in tool_calls
                    if isinstance(item, dict) and item.get("function", {}).get("name") == "plan_design_hints"), None)
        if call is None:
            content += "\nCall plan_design_hints now; a plain-text reply isn't usable here."
            continue
        try:
            args = call.get("arguments")
            if isinstance(args, str):
                args = json.loads(args)
            if isinstance(args, dict):
                _normalize_adjacency_preferences(args)
            return DesignHints.model_validate(args)
        except (ValidationError, ValueError, TypeError) as exc:
            content += f"\nYour last submission was invalid ({exc}); resubmit plan_design_hints correctly."
            continue
    print("  [hints] no usable design hints after retry; continuing with defaults", flush=True)
    return DesignHints()


def generate_layout(spec: HouseSpec, previous_layout: Layout | None = None,
                    edit_request: str | None = None, design_context: str | None = None) -> tuple[Layout | None, list[str]]:
    if not spec.is_ready_for_layout():
        return None, ["Need plot width, plot length and bedroom/bathroom counts before generation."]
    if (spec.num_floors or 1) != 1:
        return None, ["Generation currently supports one floor. Multi-floor design needs stairs and landings; please request a single-floor plan."]
    hints = _get_design_hints(spec, edit_request, design_context)
    layout = solve_layout(spec, hints)
    if layout is None:
        if spec.attached_bathroom_bedrooms:
            relaxed_spec = spec.model_copy(update={"attached_bathroom_bedrooms": None})
            if solve_layout(relaxed_spec, hints) is not None:
                return None, ["The layout solver could not find a valid arrangement with direct access from each attached bathroom "
                              "to its assigned bedroom. This does not necessarily mean the plot is too small. "
                              "The requested assignments have been kept; try adjusting the room sizes or arrangement."]
        return None, [_infeasible_message(spec)]
    print("  [layout] solved deterministically", flush=True)
    return layout, []


EXPLAIN_INFEASIBLE_SYSTEM_PROMPT = """A deterministic floor-plan solver could not fit the user's
requested rooms onto their plot. Explain this to the user using ONLY the facts given below -
never invent numbers or rooms not mentioned. In 2-4 short, friendly, conversational sentences:
say plainly that the room list doesn't fit the plot, state the concrete shortfall, and recommend
ONE OR TWO specific, actionable changes (e.g. cut the bedroom/bathroom count by a certain amount,
drop a named must-have room, or grow the plot by roughly the shortfall). Plain text only - no
markdown, no bullet lists, no headers, no apology padding."""


def _explain_infeasibility(spec: HouseSpec, needed: float, plot_area: float) -> str | None:
    """Best-effort: turns the deterministic shortfall facts into a friendly,
    specific recommendation. Returns None on any failure so the caller falls
    back to the plain deterministic message - this never blocks the response."""
    facts = (
        f"Plot: {spec.plot_width_ft:g} x {spec.plot_length_ft:g} ft = {plot_area:.0f} sq ft.\n"
        f"Requested: {spec.num_bedrooms} bedrooms, {spec.num_bathrooms} bathrooms"
        + (f", must-have rooms: {', '.join(spec.must_have_rooms)}" if spec.must_have_rooms else "") + ".\n"
        f"Estimated realistic space needed: {needed:.0f} sq ft (about {needed - plot_area:.0f} sq ft short)."
    )
    try:
        response = chat(messages=[{"role": "system", "content": EXPLAIN_INFEASIBLE_SYSTEM_PROMPT},
                                  {"role": "user", "content": facts}],
                        timeout_seconds=float(os.environ.get("OLLAMA_REQUEST_TIMEOUT_SECONDS", "90")),
                        options={"temperature": 0.3},
                        think=os.environ.get("OLLAMA_LAYOUT_THINK", "low"))
    except Exception as exc:
        print(f"  [layout] infeasibility-explanation request failed, using plain message: {exc}", flush=True)
        return None
    return (response.get("message", {}).get("content") or "").strip() or None


def _infeasible_message(spec: HouseSpec) -> str:
    plot_area = spec.plot_width_ft * spec.plot_length_ft
    needed = estimate_min_area_sqft(spec)
    if needed <= plot_area:
        return ("Could not fit the requested rooms onto this plot without violating minimum room size, "
                "room proportions, or full-plot tiling. Try adjusting the room mix, entrance direction, "
                "or plot dimensions.")
    fallback = (f"This room list realistically needs about {needed:.0f} sq ft, but the "
                f"{spec.plot_width_ft:g}x{spec.plot_length_ft:g} ft plot only has {plot_area:.0f} sq ft "
                f"- about {needed - plot_area:.0f} sq ft short. Increase the plot size, or reduce the "
                f"bedroom/bathroom count or must-have rooms.")
    return _explain_infeasibility(spec, needed, plot_area) or fallback
