# ConstructIQ

Describe a house in chat and get a scaled 2D floor plan with room labels,
dimensions, double-line walls, door swings, open passages and exterior windows.

## Run

Install `requirements.txt` into your virtual environment and configure the
Ollama Cloud credentials using `.env.example`. Keep credentials in `.env`.

```powershell
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe agent/chat.py myhouse
```

## API backend

Start the local FastAPI service:

```powershell
./.venv/Scripts/python.exe backend/serve.py
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation. The core
flow is `POST /sessions`, `POST /sessions/{id}/messages`, then
`POST /sessions/{id}/generate`. Generation is synchronous: the request returns
when the validated 2D plan and optional Three.js viewer are ready.

The browser has two pages: the requirements chat and the 3D model with its
AI Designer editing chat. Opening or refreshing the frontend shows a project
chooser over a blurred screen. **New project** asks for a name, then creates a
fresh chat using that name; **Existing project** lists saved projects to resume.
No session is created until a nonblank name is submitted. The header and sidebar
**New project** buttons use the same naming form. **Existing projects** in the chat header opens a dropdown
of saved projects; selecting one restores its chat and **Open 3D model** link.
Once the brief is ready, **Generate 3D home** shows progress over the chat and
opens the finished 3D model in the same tab. **Open 3D model** returns to an
existing model, and its back button opens the requirements chat. Refreshing
the model keeps the user on the model page. The viewer has camera and display
controls, a **2D Plan** view, and **Print** for the diagram plus room dimensions,
positions, door connections and window directions. These controls stay on the
model page alongside the AI Designer sidebar. Generation failures
stay on the current page and show their error.

Planning streams responses from Ollama Cloud using the configured model (the
legacy `-cloud` suffix is removed for direct cloud API calls). GPT-OSS uses
`OLLAMA_THINK=low` for chat and layout-hint requests by default. Configuration
is read when each request starts. The defaults are a 45-second idle read
timeout and a 90-second total cloud request budget. AI now proposes up to three
distinct room arrangements as rectangular partition trees. Python compiles
those trees into exact geometry, adds openings and checks room areas, shapes,
circulation, entrance direction and attached bathroom assignments. Invalid
proposals receive one repair attempt with specific validation feedback, within
the same planning time budget. If no AI proposal passes, the standard layout
solver remains the fallback. When multiple proposals pass, the viewer's
**Design** selector lets you switch among them without another AI call.
Every selected option is revalidated before replacing the saved model.
Parking is capped at 250 sq ft by default in both planning paths; the user can
explicitly request a different cap. Parking cannot consume leftover plot area
without limit. In project chat, **undo**, **undo last edit**, or **revoke the
changes** restores the previous saved design without an AI request. New
revisions retain requirement snapshots; older revisions restore their saved
geometry and recover structural requirements from the room list. Original
style notes cannot be recovered when an older revision has no spec snapshot.
This supports single-floor rectangular partitions, not unrestricted geometry.
AI proposals aim for 5-10% dedicated circulation. The compiler rejects total
hallway area above 15% and corridors whose short side exceeds 6 ft. A bounded
Python sizing search adjusts proportions while retaining the proposed room
grouping. Valid proposals with less dedicated hallway area are preferred.
These are planning quality rules, not building-code certification.
A cloud HTTP 5xx gets one retry within the same request budget,
as does a connect-timeout (the connection never opened). Errors inside an
HTTP 200 response stream also get one retry, except recognizable
account/configuration errors. Partial output is discarded before retrying.
Provider error details are shown with credentials redacted; retries never
reset the request deadline. Configure limits with `.env.example`.
Three.js builds the house directly in the browser from validated scene geometry.
Generation no longer launches Blender. The browser displays service and rendering errors.
Existing-layout edits currently re-solve from the current specification and
hints rather than preserving unrelated room positions from the previous
layout. Viewer edits now interpret, validate and render in one API request.
Clarification-only replies do not trigger generation. Failed layout or 3D
builds retain the previous requirements and model; successful edits commit
the revised requirements and generated model together. Exact bedroom and
attached/common bathroom removal commands update counts and assignments
directly, while ambiguous bathroom removals ask which bathroom to remove.
The solver tries plot-scaled bedroom depths when its standard depths fail,
and retries with neutral size preferences if soft design hints prevent a fit.

Attached bathroom edits are saved in `attached_bathroom_bedrooms`: `[1, 2]`
means each of Bedrooms 1 and 2 has its own bathroom; `[2]` assigns one to
Bedroom 2, and `[]` requests only common bathrooms. These assignments survive
later edits and hint-service failures. The solver places each bathroom beside
its assigned bedroom and validates private direct access. Explicit assignments
that cannot fit produce an error instead of silently becoming common bathrooms.
Wide bedroom suites can use a compact bathroom and a connected hallway alcove,
so the bathroom does not have to span the suite's full width or depth.
The viewer refreshes only after a successful 3D build; failed builds return
their warnings and do not advertise an older model as the new revision.

In the 3D model, choose **Paint walls**, click an interior wall, and use the
bottom palette or custom color picker. Painting works during **Walk inside**;
dragging still looks around. Each room's wall side has an independent color,
including its masonry around windows and doors. **Reset wall** restores the
default and **Undo** reverses paint changes made in the current viewer session.
Colors save to the project without regenerating geometry, survive reopening,
and carry forward for matching rooms after layout edits. A stale viewer must
reload before saving paint to a newer floor plan.

Offline timeout regression checks: `./.venv/Scripts/python.exe -m unittest discover -s tests`.

## Project folders

The interface uses a midnight violet studio theme with cyan accents shared by the chat,
project chooser and 3D viewer. `frontend/studio-theme.css` holds the common
palette and styling. The Light/Dark mode choice persists across visits.

- `agent/` contains the LLM requirement agent, the LLM arrangement-proposal
  agent (`layout_agent.py`), the arrangement compiler (`ai_arrangements.py`),
  the fallback layout solver (`layout_solver.py`), validation, geometry, 2D
  renderer, 3D scene builder, Three.js renderer and browser viewer.
- `backend/` contains the FastAPI routes that expose the agent to a frontend.
- `agent/` also contains the terminal commands for chat, planning, and rendering.

Example message:

The chat command opens your default browser on the 3D model after a successful
generation and viewer preparation. For a single command without interactive chat:

```powershell
./.venv/Scripts/python.exe agent/chat.py myhouse --requirements "40 by 30 feet, 2 bedrooms, 1 bathroom, one floor, south entrance"
```

Use `--no-browser` to generate files without opening a tab. In interactive chat,
type `generate` to retry saved requirements. A failed generation never opens an
old model as if it were new. Impossible specifications (too many rooms for the
plot) still produce a clear validation error, whether from an AI-compiled
arrangement or the fallback solver.

Example interactive message:

> My plot is 40 feet wide and 30 feet long. I want 3 bedrooms, 3 bathrooms,
> a home office and pooja room, with a south-facing entrance, on one floor.

The first plan runs automatically once plot dimensions and bedroom/bathroom
counts are recorded. Say `wait` to keep discussing requirements first, or type
`generate` when ready. Edits such as `move the kitchen next to the dining area`
re-run arrangement planning and validation from the current specification.

Outputs are `data/sessions/<session>.svg` and `.png`; session JSON preserves
layout history. Each accepted revision also gets `.v1.svg`/`.v1.png`, `.v2`, etc.

The 3D step produces `<session>_scene.json` and `<session>_3d.html`.
Three.js creates walls, floors, framed windows and the optional roof directly
from the same geometry used by the 2D plan. The viewer retains furniture, room
labels, camera controls, measurements, the 2D Plan view and AI Designer edits.
Existing saved models opened through the backend use this renderer too.

The SVG and PNG remain 2D assets. New generation no longer creates Blender,
GLB or rendered 3D PNG files. Interactive 3D requires internet access to load
the pinned Three.js modules and a browser with WebGL.

Choose **Walk inside** on the 3D model to explore at eye level. Drag the view
to look around, use WASD to walk and arrow keys to look, or hold the on-screen
directional buttons on the left to move and the joystick on the right to look.
Hold a direction to walk; release to stop. Both controls work together on touchscreens.
**Go to room** jumps to a selected room.
Walls and windows block walking; doorways remain passable. Furniture is
illustrative and does not block movement. The tour shows the roof and hides
measurement overlays; **Exit walk** or Escape restores your previous view
and display settings. Use **Show measurements** in the walking controls to
toggle dimensions at eye level, with lengths in red and widths in yellow.
Top View, Reset Camera and 2D Plan also exit the tour.

Walk navigation checks: `node --test tests/test_walk_tour.cjs`.

The drawing sheet includes a revision/title block and dimensions in feet.
Room allocation is weighted by function, with bathrooms targeting 40–60 sq ft.
Default validation caps each bathroom at 80 sq ft and 75% of the smallest
same-floor bedroom. These are product planning defaults, not code requirements;
explicit larger-bathroom requests can override `max_bathroom_area_sqft` and
`max_bathroom_bedroom_ratio` in the recorded specification. Existing layouts
are unchanged until regenerated.
Areas describe rectangular room zones, not net usable areas after wall thickness.
The 3D model includes framed window panels, walls below 3 ft window sills,
and headers above 7 ft door/window openings. These are visual defaults, not
structural specifications. Furniture is illustrative, with no door-swing or
circulation-clearance checking.

```powershell
# Generate again from an existing session
./.venv/Scripts/python.exe agent/run_pipeline.py myhouse
# Render a saved plan that passes the current validator
./.venv/Scripts/python.exe agent/render_layout.py myhouse
# Offline example resembling the reference drawing
./.venv/Scripts/python.exe agent/demo_plan.py
# Generate a new example using the configured cloud model
./.venv/Scripts/python.exe agent/demo_plan.py --live
```

## How it works

The primary path now asks AI for room-partition trees, then compiles and sizes
them in `ai_arrangements.py`. Invalid candidates receive feedback; only
validated alternatives reach the Design selector. This path also checks
room-specific minimum areas and the circulation limits described above.

### Standard solver fallback

When no AI arrangement passes, generation uses the older band-based solver:

1. `spec_agent.py` records and validates requirements from tool calls.
2. `layout_agent.py` asks the model for up to three distinct room-arrangement
   proposals (each a binary partition tree over the required room catalog),
   plus fallback size/adjacency preferences and a short user-facing planning
   rationale — never raw coordinates. A failed or malformed response falls
   back to neutral hints rather than blocking generation.
3. `ai_arrangements.py` compiles each AI partition tree into exact,
   non-overlapping rectangles, applies a bounded sizing search within the
   proposed grouping (enforcing minimum areas and the bathroom cap), and adds
   doors/windows via `door_planner.py`. Only proposals that pass validation
   are offered; invalid ones get one round of feedback-driven repair. If no
   AI proposal validates, `layout_solver.py` supplies the standard
   full-width-Hallway fallback, which tiles the plot with zero gaps/overlaps
   by construction, guaranteeing circulation feasibility for free.
4. `validator.py` checks geometry, room proportions, opening clearance/alignment, entrance direction
   and a continuous path from the entrance to every room — the final gate
   for both AI-compiled and fallback layouts.
5. `renderer.py` unions shared walls and subtracts openings once. SVG and PNG use
   the same geometry. Room labels are escaped and dimensions come from coordinates.

`Door.width` is in feet; `Door.kind` is `door` or `opening`. An opening has no
door leaf or swing. A room's `windows` use the same wall/position/width convention.
Position is a fraction of the whole room wall, measured left-to-right on north/
south walls and top-to-bottom on east/west walls. JSON remains the source of truth.

## Current scope

- Generation supports one ground floor. Multiple floors require a stairs/landing
  model and are explicitly rejected. The renderer can display supplied floors in
  separate panels, but this does not validate inter-floor circulation.
- Rooms are rectangular zones. Wide passages join public zones into connected
  open spaces; arbitrary polygon rooms and nonrectangular plot boundaries are
  not supported yet.
- This is a conceptual space-planning tool: wall thickness is a drawing style;
  structural details, setbacks, furniture clearance and door-swing collisions
  are not modeled. Windows are added only to rooms on the plot exterior.
- The solver can still fail to find a valid arrangement for a genuinely
  infeasible request (too many rooms for the plot). A failed layout is not
  saved as an accepted plan. Old plans may need regeneration to pass the
  latest opening and reachability checks.

The offline example is a deliberately designed fixture, not evidence of model
generation quality. `--live` saves its separate result as `generated_plan`.
