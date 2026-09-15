"""Step 0: the chatbot that collects the user's house requirements into HouseSpec.

The model never writes the JSON itself â€” it calls the update_house_spec tool
whenever it learns something concrete, and this code merges that into the
session's HouseSpec, which is what actually gets persisted.
"""
from __future__ import annotations

import json
import re
from pydantic import ValidationError

from agent.llm_client import chat
from agent.layout_solver import estimate_min_area_sqft
from agent.schemas import ChatTurn, HouseSpec, SessionState

MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """You are AI ARCHITECT, the ConstructIQ home-design assistant.

Your sole purpose is to help users describe and plan a house. ConstructIQ
collects a home's plot, rooms, layout priorities, style, entrance direction,
and budget so it can create a validated floor plan and 3D home model.

Do not answer general-knowledge, celebrity, news, entertainment, coding, or
other unrelated questions. When a message is outside home design, give only a
brief redirect: state that AI ARCHITECT helps plan a home in ConstructIQ, then
ask for one relevant design detail. Do not explain or comment on the unrelated
topic. Never call update_house_spec for an unrelated message.

Have a natural conversation to learn: plot width and length (in feet), number
of bedrooms, number of bathrooms, number of floors, any must-have rooms
(pooja room, home office, utility, store room, etc.), style preferences, which
direction the entrance should face, and any budget notes.

Whenever the user tells you something concrete, call update_house_spec with
ONLY the fields you actually learned from them â€” omit every field you don't
have real information for. Never guess or fill in a placeholder (e.g. 0, an
empty string) for something the user hasn't told you. Ask about missing
important details (plot size and bedroom count matter most) one or two at a
time â€” don't interrogate them with a long list of questions at once. Keep
replies short and conversational.

The current recorded specification is authoritative. Never ask again for any
non-null field unless the user explicitly wants to change it. BHK means
bedrooms, hall/living and kitchen: "2 BHK" means TWO bedrooms, not two rooms
total, and does not specify bathroom count. Interpret short replies using the
last assistant question. Never infer bathroom count from bedroom count.
Distinguish a count from a room number: "both bedrooms" or "the two bedrooms"
means each bedroom, while "bedroom 2" means the numbered room. If wording such
as "in the 2 bedroom I want an attached bathroom and a common bathroom" is
ambiguous, ask whether the user wants one attached bathroom in each bedroom
plus one common bathroom, or one attached bathroom total plus one common.
Do not record a guessed bathroom total or assignment while asking this.

When the user tells you something concrete, call update_house_spec immediately
with the new or corrected facts. Plot dimensions, bedroom count and bathroom
count are the required fields; style, entrance, budget and extra rooms are
optional, not prerequisites. Every reply you send is shown to the user
verbatim, so you are the one talking to them — never leave your reply empty,
including on a turn where you also call update_house_spec. After recording an
update, respond yourself in one or two short sentences: briefly acknowledge
what you recorded, then ask specifically for the next missing required field,
in this order — plot width and length, then bedroom count, then bathroom
count. Once plot size, bedroom count and bathroom count are all recorded,
tell the user their brief is ready and to click the "Generate 3D home" button
to create their design, and invite any further changes before they generate.

ConstructIQ currently plans single-floor layouts (ground floor). If the user
asks for multiple floors, explain in your own words that only the ground
floor is supported right now and ask whether to proceed with that; once they
agree, or if they already asked for a single floor, call update_house_spec
with num_floors=1 so layout generation can proceed. Never repeatedly ask
about floors once the user has answered or moved on to other specifications.
"""

UPDATE_SPEC_TOOL = {
    "type": "function",
    "function": {
        "name": "update_house_spec",
        "description": "Record or update known requirements for the house.",
        "parameters": {
            "type": "object",
            "properties": {
                "plot_width_ft": {"type": "number"},
                "plot_length_ft": {"type": "number"},
                "num_bedrooms": {"type": "integer", "minimum": 1, "description": "At least 1 - every home needs a bedroom."},
                "num_bathrooms": {"type": "integer", "minimum": 1, "description": "At least 1 - every home needs a bathroom."},
                "num_floors": {"type": "integer"},
                "attached_bathroom_bedrooms": {
                    "type": "array", "items": {"type": "integer", "minimum": 1},
                    "description": "Complete list of bedroom numbers with their own attached bathroom, one per bedroom. E.g. both bedrooms attached -> [1,2]; only bedroom 2 -> [2]; all shared -> []. Preserve existing assignments unless asked to remove them. Counts still use num_bathrooms (including common bathrooms).",
                },
                "must_have_rooms": {"type": "array", "items": {"type": "string"}},
                "remove_must_have_rooms": {"type": "array", "items": {"type": "string"}},
                "style_notes": {"type": "string"},
                "entrance_facing": {"type": "string"},
                "budget_notes": {"type": "string"},
                "max_bathroom_area_sqft": {"type": "number", "description": "Override the default 80 sq ft bathroom cap only if the user explicitly requests larger bathrooms."},
                "max_bathroom_bedroom_ratio": {"type": "number", "description": "Override the default bathroom cap of 75% of the smallest same-floor bedroom only for an explicit oversized bathroom request."},
            },
        },
    },
}


def _merge_spec(spec: HouseSpec, updates: dict) -> None:
    if not isinstance(updates, dict):
        raise ValueError("Requirements must be an object.")
    merged = spec.model_dump()
    for key, value in updates.items():
        if value is None or key not in HouseSpec.model_fields:
            continue
        if key == "must_have_rooms" and isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise ValueError("Room names must be strings.")
            merged[key] = list(dict.fromkeys([*spec.must_have_rooms, *value]))
        elif value == "":
            continue
        else:
            merged[key] = value
    removals = updates.get("remove_must_have_rooms", [])
    if not isinstance(removals, list) or not all(isinstance(item, str) for item in removals):
        raise ValueError("remove_must_have_rooms must be an array of room names.")
    if not isinstance(merged["must_have_rooms"], list) or not all(isinstance(name, str) for name in merged["must_have_rooms"]):
        raise ValueError("must_have_rooms must be an array of room names.")
    merged["must_have_rooms"] = [name for name in merged["must_have_rooms"]
                                 if name.lower() not in {item.lower() for item in removals}]
    validated = HouseSpec.model_validate(merged)
    assignments = validated.attached_bathroom_bedrooms
    if assignments is not None:
        if len(set(assignments)) != len(assignments) or any(n < 1 or n > (validated.num_bedrooms or 0) for n in assignments):
            raise ValueError("Attached bathrooms must reference distinct existing bedroom numbers.")
        if len(assignments) > (validated.num_bathrooms or 0):
            raise ValueError("Attached bathrooms cannot exceed the total bathroom count.")
    for key in HouseSpec.model_fields:
        setattr(spec, key, getattr(validated, key))


def _explicit_bhk(text: str) -> int | None:
    # Only an unambiguous affirmative BHK statement is parsed locally. Leave
    # comparisons, alternatives and negation to the contextual model.
    matches = re.findall(r"\b(\d+)\s*[- ]?\s*b\s*\.?\s*h\s*\.?\s*k\b", text, re.I)
    if len(matches) == 1 and not re.search(r"\b(not|no|don't|instead|or|versus|vs)\b|\?", text, re.I):
        return int(matches[0])
    return None


def _feasibility_note(spec: HouseSpec) -> str:
    """A deterministic feasibility estimate injected as context so the model
    can proactively flag an unrealistic room list in its own words - before
    the user hits Generate and the deterministic solver rejects it outright."""
    if not spec.is_ready_for_layout():
        return ""
    plot_area = spec.plot_width_ft * spec.plot_length_ft
    needed = estimate_min_area_sqft(spec)
    if needed <= plot_area * 1.1:
        return ""
    return (f"\nFeasibility check (computed, not a guess): this room list realistically needs "
            f"roughly {needed:.0f} sq ft, but the plot is only {plot_area:.0f} sq ft - likely too "
            f"small to fit everything well. Proactively mention this to the user in your own words "
            f"and suggest enlarging the plot or trimming the bedroom/bathroom count or must-have "
            f"rooms, before they generate.")


def _accepts_ground_floor(user_message: str, history: list[ChatTurn]) -> bool:
    text = user_message.strip().lower()
    if re.search(r"\b(ground floor|single floor|1 floor|one floor)\b", text):
        return True
    if history:
        last = history[-1]
        if last.role == "assistant" and "supports a single floor" in last.content.lower():
            if not re.search(r"^\s*(no|never|cancel|stop|don't|not)\b", text):
                return True
    return False


def run_turn(session: SessionState, user_message: str, auto_generate: bool = False) -> str:
    if _accepts_ground_floor(user_message, session.history):
        session.spec.num_floors = 1
    bhk = _explicit_bhk(user_message)
    if bhk is not None:
        _merge_spec(session.spec, {"num_bedrooms": bhk, "must_have_rooms": ["Living Room", "Kitchen"]})
    system_content = (SYSTEM_PROMPT +
                     "\nCurrent recorded specification: " + session.spec.model_dump_json() +
                     "\nBathroom count is required before generating. Normalize entrance direction to north, south, east or west. "
                     "Use remove_must_have_rooms to remove previously requested spaces.")
    if auto_generate:
        system_content += ("\nThis message comes from the 3D viewer's own \"AI Designer\" panel, which "
                          "rebuilds the design automatically right after your reply - there is no "
                          "\"Generate 3D home\" button here. Never tell the user to click it or to "
                          "generate manually; instead say the update is being applied/rebuilt now.")
    if session.layout:
        system_content += "\nCurrent floor plan (use its room names and connections when interpreting edits): " + session.layout.model_dump_json()
    system_content += ("\nRecord attached/ensuite bathroom assignments in attached_bathroom_bedrooms, "
                       "including when the user says put a bathroom in bedroom2. Keep all other recorded "
                       "assignments unless the user changes them. Acknowledge requested changes, but never "
                       "claim the model has already changed before generation succeeds.")
    system_content += _feasibility_note(session.spec)
    messages = [{"role": "system", "content": system_content}]
    messages.extend(turn.model_dump() for turn in session.history[-12:])
    messages.append({"role": "user", "content": user_message})

    last_content = ""
    for _ in range(MAX_TOOL_ROUNDS):
        # Low (not zero) temperature: this call decides real facts - does
        # reducing a count still respect a minimum, was a field already
        # recorded, etc. - so it needs to be consistent, not just varied
        # phrasing. A field-update decision flip-flopping between identical
        # requests (observed: the same "remove one of 2 bathrooms" request
        # sometimes correctly went to 1, sometimes was wrongly refused as
        # violating the 1-bathroom minimum) is a reliability problem, not a
        # style one.
        response = chat(messages=messages, tools=[UPDATE_SPEC_TOOL], options={"temperature": 0.2})
        msg = response["message"]
        content = (msg.get("content") or "").strip()
        if content:
            last_content = content

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            break

        messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tool_calls})
        tool_failed = False
        for call in tool_calls:
            fn = call["function"]
            if fn["name"] == "update_house_spec":
                try:
                    args = fn["arguments"]
                    if isinstance(args, str):
                        args = json.loads(args)
                    _merge_spec(session.spec, args)
                    result = "Recorded: " + session.spec.model_dump_json()
                except (ValidationError, ValueError, TypeError) as exc:
                    tool_failed = True
                    result = f"Invalid requirements; correct the tool call: {exc}"
                messages.append({"role": "tool", "tool_name": fn["name"], "content": result})
            else:
                messages.append({"role": "tool", "tool_name": fn["name"], "content": "Unknown tool; use update_house_spec."})

        if tool_failed:
            last_content = ""
        if content and not tool_failed:
            # The model already talked to the user alongside its tool call; no
            # need for another round trip just to re-elicit the same reply.
            break

    if bhk is not None:
        session.spec.num_bedrooms = bhk
    if _accepts_ground_floor(user_message, session.history):
        session.spec.num_floors = 1

    reply_text = last_content.strip() or "Could you tell me more about the home you'd like to design?"
    if session.spec.is_ready_for_layout() and not auto_generate:
        if "generate" not in reply_text.lower():
            reply_text += " Your brief is ready. Click the Generate 3D home button to create your design, or let me know if you would like any other adjustments."
    session.history.extend([ChatTurn(role="user", content=user_message), ChatTurn(role="assistant", content=reply_text)])
    session.history[:] = session.history[-12:]
    return reply_text
