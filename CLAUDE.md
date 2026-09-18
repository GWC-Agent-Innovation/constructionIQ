# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ConstructIQ is a chat-based dream-house designer. A user describes their
requirements in plain language; an LLM extracts those requirements and
proposes room arrangements as design judgment; deterministic code compiles
that judgment into exact room geometry, validates it, and renders it as a 2D
floor plan; the same structured layout drives a Three.js 3D model directly in
the browser.

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
  - once generation starts, proposes up to three distinct room ARRANGEMENTS
    as binary rectangular partition trees over the required room catalog,
    plus fallback size/adjacency hints and a short user-facing rationale
  - the LLM never emits final coordinates, walls, doors or windows — only
    partition trees (relative splits) and lightweight judgment
    (agent/layout_agent.py)
      |
Arrangement compiler (agent/ai_arrangements.py) — PRIMARY layout path
  - compiles each AI partition tree into exact, non-overlapping rectangles
  - runs a bounded sizing search (up to 5000 subtree placements / 32
    candidates) that adjusts split fractions while keeping the AI's proposed
    room grouping, enforcing minimum room areas and the bathroom size cap
  - enforces circulation limits: hallway area capped at 15% of plot, hallway
    short side capped at 6 ft; proposals with less dedicated hallway area are
    preferred among otherwise-valid options
  - calls agent/door_planner.py for doors/openings/windows, then
    agent/validator.py; invalid proposals get one feedback-driven repair
    attempt before being discarded
      |
Fallback: Deterministic layout solver (agent/layout_solver.py)
  - only runs if NO AI proposal validates
  - Room Area Planner + Constraint/Layout Solver: partitions the plot into
    exact, non-overlapping rectangles (a full-width "Hallway" band hubs every
    other room to it, guaranteeing circulation and full tiling by
    construction, not by retrying)
  - Gap Detection / Auto-Repair: a defensive check/patch for float residue;
    the construction tiles exactly by design
  - agent/door_planner.py then plans aligned doors, wide public-zone
    openings, a parking driveway door, and the entrance
      |
Deterministic validator (agent/validator.py)
  - checks rooms don't overlap, all fit inside the plot boundary, every
    room has at least one door, and there's a continuous path from the
    entrance to every room
  - the final gate for BOTH the AI-compiled path and the fallback solver;
    failures mean the request is genuinely infeasible (too many rooms for
    the plot), not a "please retry" situation
      |
2D renderer
  - draws the validated layout as an SVG top-down floor plan (shared by PNG)
      |
Three.js 3D (browser-built, no server-side render step)
  - the SAME structured layout JSON drives agent's 3D scene builder, which
    Three.js turns into walls, floors, framed windows and roof directly in
    the browser
  - important: the 2D preview and the 3D model are two renderers pointed at
    one source of truth (the layout JSON), not independently generated —
    what the user approved in 2D is what gets built in 3D
  - generation no longer launches Blender and no longer produces GLB or
    rendered 3D PNG files; that path has been removed
```

When multiple AI proposals validate, the viewer's **Design** selector lets
the user switch between them without another AI call (backed by
`POST /sessions/{id}/layout-choice`); the selected option is revalidated
before it replaces the saved model.

**Edits work the same way as generation.** A user asking to change an approved
layout ("move the kitchen next to the living room") re-runs the same
spec -> arrangement -> compile/solve -> validate -> render pipeline from the
current specification (neither path preserves unrelated room positions
across an edit — see agent/layout_agent.py). There is no separate "edit
mode" — generation and editing are the same pipeline call
(`pipeline.generate_and_save`). The 3D build now happens to a temp staging
directory first and only replaces the live files if it succeeds, so a
failed edit never overwrites a working model. Every accepted layout version
is kept (not overwritten) so a user can compare or revert.

### Undo

`agent/project_undo.py` recognizes chat phrases like "undo", "undo last
edit", "revoke the changes" (`is_undo_request`) and restores the previous
saved layout version from session state (`revision_specs` /
`revision_parents` / `layout_history`) **without an AI call** — it replays
`pipeline.generate_and_save(restore_revision=...)` against the recorded
requirement snapshot for that revision. Older revisions saved before
snapshots existed reconstruct an approximate spec heuristically. This is
chat-driven undo of layout state, not file backups — see `data/backups/`
below, which is unrelated.

### Wall painting

`agent/wall_paint.js` is a browser-only feature of the Three.js viewer: pick
an interior wall (per room + wall side), choose a color, and it posts to
`POST /sessions/{id}/wall-paint`, which stores the color in the layout's
`wall_colors` without regenerating geometry. Colors persist across reloads
and carry forward for matching rooms across later layout edits. Includes a
client-side undo stack (paint changes only) and optimistic apply with
rollback on request failure.

### `data/backups/`

Not read or written by any code — it's a manual/ad-hoc scratch area where
full session JSON snapshots get saved by hand before risky manual edits.
Don't treat it as a documented feature or wire code to depend on it.

## LLM backend

Uses **Ollama's cloud-hosted models**, not a self-managed local Ollama
instance and not a different vendor's cloud API (e.g. Claude/OpenAI).
This was an explicit decision; do not swap the backend without asking.

Only use models that support tool/function calling, since the pipeline
depends on the LLM returning structured JSON (spec updates and partition-tree
arrangement proposals), not free text — e.g. `qwen2.5`, `llama3.1`,
`llama3.2`, `mistral-nemo`, `command-r`. A hint-call failure or malformed
response is never fatal — `agent/layout_agent.py` falls back to neutral
defaults and the standard solver still runs — so a smaller/weaker model
degrades design quality, not reliability.

## Key design principle

The LLM decides *what* to emphasize and how to group rooms (design judgment:
a more spacious living room, kitchen kept near dining, privacy zoning,
proposed room-partition trees) — deterministic code
(`agent/ai_arrangements.py`, with `agent/layout_solver.py` as fallback) turns
that into exact, correct-by-construction geometry, and `agent/validator.py`
is the final gate. The LLM never emits final coordinates; do not reintroduce
a path where it does.

## Commands

See README.md. Start chat with `.venv/Scripts/python.exe agent/chat.py myhouse`.
