"""Step 0: the chatbot that collects the user's house requirements into HouseSpec.

The model never writes the JSON itself â€” it calls the update_house_spec tool
whenever it learns something concrete, and this code merges that into the
session's HouseSpec, which is what actually gets persisted.
"""
from __future__ import annotations

import json
import os
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

Sound like an attentive architect having a real conversation, not a form being
filled in one field at a time. Vary your sentence structure and wording turn
to turn instead of reusing the same template (e.g. don't always open with
"Got it," or always phrase the next question the same way). React briefly and
specifically to what the user just said — a detail worth a short comment, a
number worth a quick reaction — before moving on, rather than only
acknowledging that you recorded it. Never repeat a question you've already
asked and gotten an answer to.

Whenever the user tells you something concrete, call update_house_spec with
ONLY the fields you actually learned from them â€” omit every field you don't
have real information for. Never guess or fill in a placeholder (e.g. 0, an
empty string) for something the user hasn't told you. Ask about missing
important details (plot size and bedroom count matter most) one or two at a
time â€” don't interrogate them with a long list of questions at once. Keep
replies short and conversational.

Write exactly ONE reply, one time. Never draft your answer, then continue and
restate or re-answer it again in the same turn â€” that reads as two replies
stitched together and confuses the user. If you already asked your question
once in this reply, stop; do not ask it again in different words later in the
same reply.

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

Living Room and Kitchen are always part of every home and can never be
removed - say so plainly if asked. The Dining Room is the one exception: if
the user explicitly asks to drop it or fold it into the living room, call
update_house_spec with include_dining_room=false (set it back to true if they
ask to restore it). This is the only way it actually leaves the floor plan -
writing it into style_notes alone changes nothing about the generated layout.

When the user tells you something concrete, call update_house_spec immediately
with the new or corrected facts. Plot dimensions, bedroom count and bathroom
count are the required fields; style, entrance, budget and extra rooms are
optional, not prerequisites. Every reply you send is shown to the user
verbatim, so you are the one talking to them — never leave your reply empty,
including on a turn where you also call update_house_spec. After recording an
update, respond yourself in one or two short sentences: briefly acknowledge
what you recorded, then ask specifically for the next missing required field,
in this order — plot width and length, then bedroom count, then bathroom
count.

THE BRIEF IS COMPLETE (plot size, bedroom count and bathroom count are all
now recorded): the context below tells you whether this is the FIRST time
that's true in this conversation, or whether it was already true before this
message.
- First time: this reply's job is the recap, not another question. Open by
  explicitly restating the plot size, bedroom count and bathroom count
  (mention attached/ensuite assignments if any) in one natural sentence —
  these three are why the brief just became ready, so always name all three,
  even if you already acknowledged them a moment ago. Then, in a second
  sentence, mention any must-have rooms, style notes, entrance facing and
  budget notes that were given, or note that none were specified if none
  were. Write it as real sentences, not a bullet list or field dump; you may
  still ask about one optional detail you don't have yet, but the recap comes
  first. Then tell them to click the "Generate 3D home" button when ready,
  and invite any final changes first.
- Already ready before this message: don't repeat the full recap again. Just
  respond naturally to whatever they said this turn (a change, a question, or
  small talk), and only mention generating again if they ask or if you just
  applied a change worth confirming.

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
                "must_have_rooms": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Keep a size word the user actually used (e.g. \"small parking\", \"compact parking\") "
                                    "in the room name instead of normalizing it away to just \"Parking\" - deterministic "
                                    "sizing reads that word to size the room smaller. Only do this when the user said it.",
                },
                "remove_must_have_rooms": {"type": "array", "items": {"type": "string"}},
                "include_dining_room": {
                    "type": "boolean",
                    "description": "Set to false ONLY when the user explicitly asks to drop the separate dining "
                                    "room (e.g. fold it into the living room, remove the dining room). Set back to "
                                    "true if they ask to restore it. Living Room and Kitchen can never be dropped - "
                                    "say so if asked. Never merely claim in style_notes that the dining room was "
                                    "removed without also setting this field; that leaves it in the actual layout.",
                },
                "style_notes": {"type": "string"},
                "required_adjacencies": {
                    "type": "array", "maxItems": 20,
                    "items": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]},
                    "description": "Complete list of explicitly requested neighboring room pairs. Record move/place X near/next to Y here, not only in style_notes. Preserve previous pairs unless the user changes them. This is checked against actual geometry.",
                },
                "entrance_facing": {"type": "string"},
                "budget_notes": {"type": "string"},
                "max_bathroom_area_sqft": {"type": "number", "description": "Override the default 80 sq ft bathroom cap only if the user explicitly requests larger bathrooms."},
                "max_parking_area_sqft": {"type": "number", "description": "Default parking cap is 250 sq ft. Override only when the user explicitly specifies a larger parking area. Do not enlarge it to make a generated layout pass."},
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


def _recover_spec_json(content: str) -> dict | None:
    """Best-effort recovery for an observed failure mode: instead of an
    actual tool call, the model writes the update_house_spec call's
    ARGUMENTS as plain-text JSON in its reply (e.g. a bare
    {"style_notes": "...", "must_have_rooms": [...]} object). Returns the
    parsed arguments only when they plausibly ARE those arguments - every
    top-level key a real field the tool accepts - never for ordinary prose
    that merely happens to contain braces."""
    if not content:
        return None
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        return None
    try:
        candidate = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(candidate, dict) or not candidate:
        return None
    valid_keys = set(HouseSpec.model_fields) | {"remove_must_have_rooms"}
    if not all(key in valid_keys for key in candidate):
        return None
    return candidate


def _explicit_bhk(text: str) -> int | None:
    # Only an unambiguous affirmative BHK statement is parsed locally. Leave
    # comparisons, alternatives and negation to the contextual model.
    matches = re.findall(r"\b(\d+)\s*[- ]?\s*b\s*\.?\s*h\s*\.?\s*k\b", text, re.I)
    if len(matches) == 1 and not re.search(r"\b(not|no|don't|instead|or|versus|vs)\b|\?", text, re.I):
        return int(matches[0])
    return None


def _first_reply_segment(text: str) -> str:
    """The model occasionally splices two draft replies together with no
    separating space (observed e.g. "...utility space?Excellent! Here's what
    we have so far...") - effectively answering twice in one turn, which is
    exactly what reads as repetitive/robotic. Sentence-ending punctuation is
    essentially never followed directly by a capital letter with no space in
    genuine prose, so that boundary is a reliable place to cut the splice."""
    match = re.search(r"[.!?](?=[A-Z])", text)
    if match and match.start() > 20:
        return text[:match.start() + 1]
    return text


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


def _room_removal(session: SessionState, text: str) -> str | None:
    """Resolve simple, exact deletion commands without probabilistic counts.

    Compound requests and descriptions still go to the requirement agent.
    An ambiguous bathroom deletion asks for a target rather than guessing.
    """
    match = re.fullmatch(r"(?:please\s+)?(?:remove|delete)\s+(?:the\s+)?(.+?)[.!]?", text.strip(), re.I)
    if not match:
        return None
    target = re.sub(r"[.\s]+", " ", match[1].lower()).strip()
    spec = session.spec
    if target == "dining room":
        if not spec.include_dining_room:
            return "The dining room is already removed from the current requirements."
        _merge_spec(spec, {"include_dining_room": False})
        return "Recorded removal of the dining room; its space folds into the living room."
    if not spec.num_bathrooms or not spec.num_bedrooms:
        return None
    assignments = spec.attached_bathroom_bedrooms
    if assignments is None and session.layout:
        bedrooms = {r.id: int(r.name.rsplit(' ', 1)[1]) for r in session.layout.rooms
                    if re.fullmatch(r"Bedroom \d+", r.name)}
        assignments = sorted(bedrooms[r.ensuite_for] for r in session.layout.rooms
                             if r.ensuite_for in bedrooms)
    if assignments is None:
        return None
    updates = {}
    attached = re.fullmatch(r"(?:att bath|attached bath(?:room)?|ensuite)\s*(\d+)", target)
    bedroom = re.fullmatch(r"bedroom\s*(\d+)", target)
    if target in ("common bathroom", "shared bathroom"):
        common = spec.num_bathrooms - len(assignments)
        if common == 0:
            return "There is no common bathroom in the current requirements."
        if common > 1:
            return "There is more than one common bathroom. Which bathroom should I remove?"
        updates = {"num_bathrooms": spec.num_bathrooms - 1,
                   "attached_bathroom_bedrooms": assignments}
    elif attached:
        number = int(attached[1])
        if number not in assignments:
            return f"Bedroom {number} does not have an attached bathroom in the current requirements."
        updates = {"num_bathrooms": spec.num_bathrooms - 1,
                   "attached_bathroom_bedrooms": [n for n in assignments if n != number]}
    elif bedroom:
        number = int(bedroom[1])
        if not 1 <= number <= spec.num_bedrooms:
            return f"There is no Bedroom {number} in the current requirements."
        if spec.num_bedrooms == 1:
            return "The current home planner requires at least one bedroom."
        updates = {"num_bedrooms": spec.num_bedrooms - 1,
                   "num_bathrooms": spec.num_bathrooms - int(number in assignments),
                   "attached_bathroom_bedrooms": [n - int(n > number) for n in assignments if n != number]}
    elif target in ("bathroom", "a bathroom", "one bathroom"):
        if spec.num_bathrooms == 1:
            return "The current home planner requires at least one bathroom."
        return "Which bathroom should I remove: a common bathroom or an attached bathroom (specify its bedroom number)?"
    else:
        names = [name for name in spec.must_have_rooms if name.lower().strip() == target]
        if names:
            updates = {"remove_must_have_rooms": names}
    if not updates:
        return None
    if updates.get("num_bathrooms", spec.num_bathrooms) < 1:
        return "That would remove the last bathroom. The current home planner requires at least one bathroom."
    _merge_spec(spec, updates)
    return f"Recorded removal of {target}."


def run_turn(session: SessionState, user_message: str, auto_generate: bool = False,
            tool_called_out: list[bool] | None = None) -> str:
    text = re.sub(r'\s+', ' ', user_message.strip().lower().replace('ketchen', 'kitchen')).rstrip('.!')
    near = re.fullmatch(r'(?:please )?(?:(?:attach|move|place|put|keep|position) )?(?:the )?'
                        r'(kitchen|dining(?: room)?) (?:near|next to|adjacent to|beside) (?:the )?'
                        r'(kitchen|dining(?: room)?)', text)
    if near and 'kitchen' in (near[1], near[2]) and near[1] != near[2]:
        pairs = [item.model_dump() for item in session.spec.required_adjacencies]
        if not any({item['a'].lower(), item['b'].lower()} == {'kitchen', 'dining room'} for item in pairs):
            pairs.append({'a': 'Kitchen', 'b': 'Dining Room'})
        _merge_spec(session.spec, {'required_adjacencies': pairs})
        reply = 'Recorded: the kitchen must share a wall with the dining room. The updated plan will be checked before it is saved.'
        session.history.extend([ChatTurn(role='user', content=user_message), ChatTurn(role='assistant', content=reply)])
        session.history[:] = session.history[-12:]
        if tool_called_out is not None:
            tool_called_out.append(True)
        return reply
    was_ready = session.spec.is_ready_for_layout()
    spec_before_removal = session.spec.model_copy(deep=True)
    removal_reply = _room_removal(session, user_message)
    if removal_reply is not None:
        session.history.extend([ChatTurn(role="user", content=user_message),
                                ChatTurn(role="assistant", content=removal_reply)])
        if tool_called_out is not None:
            # Several _room_removal outcomes are clarifying questions or
            # refusals (ambiguous target, already-last bathroom, etc.) that
            # never touch the spec - only report an attempt when it did.
            tool_called_out.append(session.spec != spec_before_removal)
        return removal_reply
    if _accepts_ground_floor(user_message, session.history):
        session.spec.num_floors = 1
    bhk = _explicit_bhk(user_message)
    if bhk is not None:
        _merge_spec(session.spec, {"num_bedrooms": bhk, "must_have_rooms": ["Living Room", "Kitchen"]})
    system_content = (SYSTEM_PROMPT +
                     "\nCurrent recorded specification: " + session.spec.model_dump_json() +
                     "\nBathroom count is required before generating. Normalize entrance direction to north, south, east or west. "
                     "Use remove_must_have_rooms to remove previously requested spaces.")
    system_content += ("\nWas the brief already complete (plot size, bedroom count and bathroom count all "
                       "recorded) before this message? " + ("Yes." if was_ready and not auto_generate else "No."))
    if auto_generate:
        system_content += ("\nThis message comes from the 3D viewer's own \"AI Designer\" panel, which "
                          "rebuilds the design automatically right after your reply - there is no "
                          "\"Generate 3D home\" button here. Never tell the user to click it or to "
                          "generate manually; instead say the update is being applied/rebuilt now.")
        system_content += ("\nEvery actionable edit must call update_house_spec. Record relative size and "
                           "preferences in style_notes, retaining existing preferences. Record explicit "
                           "near/next-to room requests in required_adjacencies so geometry validation can "
                           "enforce them. Do not record room adjacency solely in style_notes. "
                           "When correcting a room-name typo, remove the old name with remove_must_have_rooms "
                           "and add the corrected name; do not create two rooms. When removing a bedroom, "
                           "also remove its attached bathroom, adjust the total bathroom count and renumber "
                           "remaining attached assignments. Never refuse to remove one of several bathrooms "
                           "on the grounds that a home needs at least one. If the target is ambiguous, ask "
                           "which room without changing any fields. Unsupported features must be explained "
                           "without claiming success. Changes are provisional until the build succeeds.")
        system_content += ("\nOnly address exactly what this message asks to change. Never proactively ask "
                           "about optional fields (entrance facing, style, budget, or any other unset optional "
                           "field) here - that belongs to initial requirement gathering, not every edit. If the "
                           "user has ever declined to specify one (\"leave it as is\", \"doesn't matter\", "
                           "\"no preference\", etc.), never bring it up again for the rest of this session.")
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
    nudged_for_missing_tool_call = False
    attempted_tool_call = False
    for _ in range(MAX_TOOL_ROUNDS):
        # Low (not zero) temperature: this call decides real facts - does
        # reducing a count still respect a minimum, was a field already
        # recorded, etc. - so it needs to be consistent, not just varied
        # phrasing. A field-update decision flip-flopping between identical
        # requests (observed: the same "remove one of 2 bathrooms" request
        # sometimes correctly went to 1, sometimes was wrongly refused as
        # violating the 1-bathroom minimum) is a reliability problem, not a
        # style one.
        response = chat(messages=messages, tools=[UPDATE_SPEC_TOOL], options={"temperature": 0.2},
                        think=os.environ.get("OLLAMA_SPEC_THINK", "medium"))
        msg = response["message"]
        content = (msg.get("content") or "").strip()
        if content:
            last_content = content

        tool_calls = msg.get("tool_calls")
        recovered_args = _recover_spec_json(content) if not tool_calls else None
        if recovered_args is not None:
            # Observed failure mode: instead of an actual tool call, the
            # model writes the update_house_spec ARGUMENTS as plain-text
            # JSON in its reply (e.g. {"style_notes": "...", ...}). Treat it
            # as the tool call it was clearly trying to make - feeding the
            # same synthetic assistant/tool turns the real tool-call path
            # would produce - rather than showing raw JSON to the user or
            # spending the one nudge retry on something nudging alone won't
            # reliably fix (the model already "tried"; it just used the
            # wrong channel).
            attempted_tool_call = True
            messages.append({"role": "assistant", "content": ""})
            try:
                _merge_spec(session.spec, recovered_args)
                result = "Recorded: " + session.spec.model_dump_json()
            except (ValidationError, ValueError, TypeError) as exc:
                result = f"Invalid requirements; correct the tool call: {exc}"
            messages.append({"role": "tool", "tool_name": "update_house_spec", "content": result})
            last_content = ""  # let the next round produce a real reply
            continue
        if not tool_calls:
            if (auto_generate and content and "?" not in content
                    and not nudged_for_missing_tool_call and not attempted_tool_call):
                # Observed failure mode: the model describes applying a change
                # (no question mark, so not a clarifying question) but never
                # actually calls update_house_spec - the request is then
                # silently a no-op even though the reply claims success. One
                # corrective nudge, not a silent pass-through. Only fires when
                # NO tool call was attempted yet this turn - a round that
                # follows an already-attempted (even failed) tool call is
                # legitimately explaining that outcome, not falsely claiming
                # success, and shouldn't be second-guessed.
                nudged_for_missing_tool_call = True
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "system", "content":
                                 "You did not call update_house_spec, and your reply did not ask a "
                                 "clarifying question either - it read as though you applied a change "
                                 "without recording it. If this is an actionable request, call "
                                 "update_house_spec now with the concrete field changes. If you genuinely "
                                 "need more information first, ask one specific question instead."})
                continue
            break

        attempted_tool_call = True
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

    reply_text = _first_reply_segment(last_content.strip()) or "Could you tell me more about the home you'd like to design?"
    if session.spec.is_ready_for_layout() and not auto_generate:
        if "generate" not in reply_text.lower():
            reply_text += " Your brief is ready. Click the Generate 3D home button to create your design, or let me know if you would like any other adjustments."
    session.history.extend([ChatTurn(role="user", content=user_message), ChatTurn(role="assistant", content=reply_text)])
    if tool_called_out is not None:
        tool_called_out.append(attempted_tool_call)
    return reply_text
