# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ConstructIQ is a chat-based dream-house designer. A user describes their
requirements in plain language; an LLM extracts those requirements and
contributes lightweight design judgment (room-size emphasis, adjacency
preferences); a deterministic solver turns that into exact room geometry;
deterministic code validates and renders that layout as a 2D floor plan; the
same structured layout later drives a 3D model/render.

The Python prototype is implemented. See README.md for current commands and
scope. Deterministic code plans room placement, aligned doors, open public
passages and exterior windows. SVG and PNG share the same geometry.
Generation currently supports one floor, with room counts and entrance access
validated before accepting a plan.

## Architecture

```
User chat message
      |
LLM (via Ollama — see "LLM backend" below)
  - gathers requirements conversationally (plot dimensions, bedroom/bathroom
    count, must-have rooms, style, entrance orientation) -> agent/spec_agent.py
  - once generation starts, contributes DesignHints: relative room-size
    emphasis and adjacency preferences (e.g. "spacious living room", "keep
    kitchen next to dining") plus a short user-facing rationale
  - the LLM never emits coordinates, walls, doors or windows — only this
    lightweight judgment (agent/layout_agent.py)
      |
Deterministic layout solver (agent/layout_solver.py)
  - Room Area Planner: builds the room list from the spec (bedrooms,
    bathrooms split attached/common, must-have rooms) and assigns each a
    target area, shaped by the LLM's DesignHints
  - Constraint/Layout Solver: partitions the plot into exact, non-overlapping
    rectangles (a full-width "Hallway" band hubs every other room to it,
    guaranteeing circulation and full tiling by construction, not by retrying)
  - Gap Detection / Auto-Repair: a defensive check/patch for float residue;
    the construction tiles exactly by design
  - agent/door_planner.py then plans aligned doors, wide public-zone
    openings, a parking driveway door, and the entrance
      |
Deterministic validator (agent/validator.py)
  - checks rooms don't overlap, all fit inside the plot boundary, every
    room has at least one door, and there's a continuous path from the
    entrance to every room
  - since the solver's construction is correct by design, this should nearly
    always pass; failures mean the request is genuinely infeasible (too many
    rooms for the plot), not a "please retry" situation
      |
2D renderer
  - draws the validated layout as an SVG top-down floor plan
      |
3D conversion via Blender (headless)
  - the SAME structured layout JSON is handed to Blender to extrude walls
    and build 3D geometry, then rendered
  - important: the 2D preview and the 3D model are two renderers pointed at
    one source of truth (the layout JSON), not independently generated —
    what the user approved in 2D is what gets built in 3D
```

**Edits work the same way as generation.** A user asking to change an approved
layout ("move the kitchen next to the living room") re-runs the same
spec -> hints -> solve -> validate -> render pipeline from the current
specification (the solver does not yet preserve unrelated room positions
across an edit — see agent/layout_agent.py). There is no separate "edit
mode" — generation and editing are the same pipeline call. Every accepted
layout version should be kept (not overwritten) so a user can compare or
revert.

## LLM backend

Uses **Ollama's cloud-hosted models**, not a self-managed local Ollama
instance and not a different vendor's cloud API (e.g. Claude/OpenAI).
This was an explicit decision; do not swap the backend without asking.

Only use models that support tool/function calling, since the pipeline
depends on the LLM returning structured JSON (spec updates and design
hints), not free text — e.g. `qwen2.5`, `llama3.1`, `llama3.2`,
`mistral-nemo`, `command-r`. A hint-call failure or malformed response is
never fatal — `agent/layout_agent.py` falls back to neutral defaults and the
solver still runs — so a smaller/weaker model degrades design quality, not
reliability.

## Key design principle

The LLM decides *what* to emphasize (design judgment: a more spacious living
room, kitchen kept near dining, privacy zoning) — deterministic code
(`agent/layout_solver.py`) turns that into exact, correct-by-construction
geometry, and `agent/validator.py` is the final gate. The LLM never emits
coordinates; do not reintroduce a path where it does.

## Commands

See README.md. Start chat with `.venv/Scripts/python.exe agent/chat.py myhouse`.
