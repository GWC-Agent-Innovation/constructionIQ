"""Builds a self-contained interactive 3D viewer (HTML) from a house layout
and its rendered .glb/.png, so a session's 3D model can be dragged/orbited in
any browser with no server and no Blender at view time.

Walls/roof/floor come from the .glb (built by Blender via blender_script.py).
Furniture and room-name labels are placeholder boxes/sprites built directly
in-browser from the layout JSON, so they always match the current layout
without needing another Blender pass.
"""
from __future__ import annotations

import base64
import json
from html import escape

from agent.schemas import HouseSpec, Layout
from agent.renderer import render_svg


THREE_VERSION = "0.160.0"

# One category taxonomy drives both the 3D placeholder furniture AND the
# room-schedule's colored category dot, so the two views read as one system.
CATEGORY_COLORS = {
    "bedroom": "#60a5fa",
    "bathroom": "#f472b6",
    "kitchen": "#fb923c",
    "dining": "#facc15",
    "living": "#34d399",
    "pooja": "#c9a44c",
    "office": "#38bdf8",
    "car_parking": "#94a3b8",
    "bike_parking": "#94a3b8",
}
DEFAULT_CATEGORY_COLOR = "#8b98a3"


def _furniture_category(name: str) -> str | None:
    n = name.lower()
    if "bath" in n or "toilet" in n or "wc" in n:
        return "bathroom"
    if "bed" in n:
        return "bedroom"
    if "kitchen" in n:
        return "kitchen"
    if "dining" in n:
        return "dining"
    if "pooja" in n or "puja" in n:
        return "pooja"
    if "office" in n or "study" in n:
        return "office"
    if "car" in n and "park" in n:
        return "car_parking"
    if "bike" in n and "park" in n:
        return "bike_parking"
    if "living" in n or "lounge" in n or "family" in n:
        return "living"
    return None


def build_viewer_html(layout: Layout, spec: HouseSpec, glb_bytes: bytes, poster_png_bytes: bytes,
                      project_name: str | None = None) -> str:
    glb_b64 = base64.b64encode(glb_bytes).decode("ascii")
    poster_b64 = base64.b64encode(poster_png_bytes).decode("ascii")

    rooms = sorted(layout.rooms, key=lambda r: -(r.width * r.length))
    total_area = sum(r.width * r.length for r in layout.rooms)
    plot_w, plot_l = layout.plot_width_ft, layout.plot_length_ft
    title = escape(project_name or "Residential Design")

    furnish_rooms = json.dumps([
        {
            "id": r.id, "name": r.name, "category": _furniture_category(r.name),
            "x": r.x, "y": r.y, "width": r.width, "length": r.length, "floor": r.floor,
            "doors": [door.model_dump() for door in r.doors],
            "windows": [window.model_dump() for window in r.windows],
        }
        for r in layout.rooms
    ]).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    entrance = escape((spec.entrance_facing or "unset").capitalize())
    style_notes = escape(spec.style_notes or "Residential")
    plan_svg = render_svg(layout)
    room_names = {room.id: room.name for room in layout.rooms}
    room_names['outside'] = 'Outside'
    alignment_rows = []
    for room in sorted(layout.rooms, key=lambda r: (r.floor, r.y, r.x)):
        doors = '; '.join(
            f"{door.wall.capitalize()} to {room_names.get(door.connects_to, door.connects_to or 'Unspecified')} "
            f"({door.width:.1f} ft {door.kind})" for door in room.doors) or 'None'
        windows = '; '.join(f"{window.wall.capitalize()} ({window.width:.1f} ft)" for window in room.windows) or 'None'
        alignment_rows.append(
            f'<tr><td>{escape(room.name)}</td><td>{room.floor}</td>'
            f'<td>{room.width:.2f} × {room.length:.2f}</td><td>{room.width * room.length:.1f}</td>'
            f'<td>{room.x:.2f}, {room.y:.2f}</td><td>{escape(doors)}</td><td>{escape(windows)}</td></tr>')
    alignment_html = ''.join(alignment_rows)


    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | ConstructIQ</title>
<script>

if (new URLSearchParams(location.search).get('embed') === '1') document.documentElement.classList.add('embedded');
</script>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {{
  color-scheme: dark;
  --bg: #0c120e;
  --panel: #111a14;
  --panel-2: #0e1511;
  --line: #1d2b21;
  --ink: #edf2ee;
  --muted: #82968a;
  --accent: #2e8b57;
  --accent-hover: #38a169;
  --accent-light: #16281c;
  --radius: 14px;
}}

* {{ box-sizing: border-box; }}
html, body {{ margin: 0; }}
body {{
  background: var(--bg);
  color: var(--ink);
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
  min-height: 100dvh;
}}
.app-shell {{ max-width: 1920px; margin: 0 auto; padding: 18px clamp(14px, 3vw, 32px) 24px; }}

/* ---------------------------------------------------------------------
   Top bar: back, title block, view/camera toolbar, compass
   --------------------------------------------------------------------- */
.viewer-topbar {{
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 12px 16px; margin-bottom: 16px;
}}
.icon-btn {{
  display: grid; place-items: center; width: 38px; height: 38px; flex: none;
  background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px;
  color: var(--ink); cursor: pointer; transition: border-color .15s, background .15s;
}}
.icon-btn:hover {{ border-color: var(--accent); background: var(--accent-light); }}
.icon-btn svg {{ width: 18px; height: 18px; stroke: currentColor; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }}
.title-block {{ min-width: 0; }}
.title-block h1 {{
  font-size: clamp(1.1rem, 2.4vw, 1.4rem); font-weight: 700; margin: 0; letter-spacing: -0.01em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}
.title-block .subtitle {{ margin: 2px 0 0; font-size: 0.8rem; color: var(--muted); }}
.view-toolbar {{
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-left: auto;
}}
.segmented {{ display: flex; gap: 3px; background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; padding: 3px; }}
.segmented button {{
  font: inherit; font-size: 0.82rem; font-weight: 500; padding: 7px 14px; border: none; border-radius: 8px;
  background: transparent; color: var(--muted); cursor: pointer; transition: background .15s, color .15s;
}}
.segmented button[aria-pressed="true"] {{ background: var(--accent); color: #ffffff; }}
.segmented button:hover:not([aria-pressed="true"]) {{ color: var(--ink); }}
.toolbar-actions {{ display: flex; gap: 6px; flex-wrap: wrap; }}
.toolbar-actions button {{
  font: inherit; font-size: 0.82rem; font-weight: 500; padding: 8px 14px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--panel-2); color: var(--ink); cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
}}
.toolbar-actions button:hover {{ border-color: var(--accent); }}
.toolbar-actions svg {{ width: 15px; height: 15px; stroke: currentColor; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }}
.compass {{
  display: grid; place-items: center; width: 38px; height: 38px; flex: none; border-radius: 50%;
  background: var(--panel-2); border: 1px solid var(--line); font-family: 'IBM Plex Mono', monospace;
  font-size: 0.68rem; font-weight: 600; color: var(--muted);
}}

/* ---------------------------------------------------------------------
   Body: editing sidebar + 3D model
   --------------------------------------------------------------------- */
.viewer-workspace {{ display: grid; grid-template-columns: minmax(300px, 360px) minmax(0, 1fr); gap: 16px; height: calc(100dvh - 170px); min-height: 520px; }}
.viewer-body {{ min-width: 0; min-height: 0; }}

.stage-card {{ display: flex; flex-direction: column; height: 100%; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }}
.stage-wrap {{ flex: 1; min-height: 0; position: relative; width: 100%; background: #05070a; overflow: hidden; }}
.stage-wrap canvas {{ display: block; width: 100%; height: 100%; cursor: grab; }}
.stage-wrap canvas:active {{ cursor: grabbing; }}
.stage-wrap .poster {{
  position: absolute; inset: 0; background: #05070a;
  display: flex; align-items: center; justify-content: center; transition: opacity 0.4s ease;
}}
.stage-wrap .poster img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}
.stage-wrap.is-ready .poster {{ opacity: 0; pointer-events: none; }}
.hud-caption {{ position: absolute; top: 14px; left: 16px; font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; color: rgba(237,242,238,0.6); }}
.hud-caption.is-error {{ color: #f0a58e; }}
.measurement-note {{ position: absolute; top: 42px; left: 16px; right: 16px; max-width: 420px; padding: 8px 10px; border-radius: 8px; background: #111a14e8; color: #f5df96; font-size: 11px; pointer-events: none; }}

.toggle-row {{
  position: absolute; left: 14px; bottom: 14px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  max-width: calc(100% - 28px);
}}
.toggle-row-label {{
  font-family: 'IBM Plex Mono', monospace; font-size: 0.66rem; letter-spacing: 0.08em; text-transform: uppercase;
  color: rgba(237,242,238,0.55); margin-right: 2px;
}}
.chip {{
  display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; padding: 6px 12px;
  background: rgba(17,26,20,0.82); backdrop-filter: blur(6px); border: 1px solid rgba(237,242,238,0.14);
  border-radius: 999px; color: #edf2ee; cursor: pointer; user-select: none;
}}
.chip input {{ accent-color: var(--accent); }}
.chip:has(input:checked) {{ border-color: var(--accent); background: rgba(46,139,87,0.28); }}

.plan-view {{ flex: 1; min-height: 0; overflow: auto; background: #fbfdfb; color: #18251d; padding: 20px; }}
.plan-view svg {{ display: block; width: 100%; height: auto; }}
.plan-heading h2 {{ margin: 0 0 6px; }}
.plan-heading p, .alignment-details p {{ font-size: 12px; line-height: 1.5; }}
.alignment-details {{ margin-top: 24px; }}
.alignment-table-wrap {{ overflow-x: auto; }}
.alignment-details table {{ min-width: 650px; font-size: 12px; }}
.alignment-details th, .alignment-details td {{ color: #18251d; border-color: #ccd6ce; padding: 8px; vertical-align: top; }}
.toolbar-actions button[aria-pressed="true"] {{ background: var(--accent); color: white; }}
.drawing-note {{ padding: 10px 16px; margin: 0; font: 11px 'IBM Plex Mono', monospace; color: var(--muted); border-top: 1px solid var(--line); }}

.panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 18px 8px; }}
.panel h2 {{ font-size: 1.15rem; font-weight: 700; margin: 0 0 2px; }}
.panel .sub {{ font-size: 0.78rem; color: var(--muted); margin: 0 0 14px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
th {{
  text-align: left; font-family: 'IBM Plex Mono', monospace; font-weight: 500; font-size: 0.66rem;
  letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted);
  border-bottom: 1px solid var(--line); padding: 0 6px 8px 0;
}}
td {{ padding: 8px 6px 8px 0; border-bottom: 1px solid var(--line); }}
td.num, th.num {{ font-family: 'IBM Plex Mono', monospace; font-variant-numeric: tabular-nums; text-align: right; color: var(--muted); }}
tr:last-child td {{ border-bottom: none; }}
.room-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }}
.table-wrap {{ max-height: 460px; overflow-y: auto; }}
.total-row {{
  display: flex; justify-content: space-between; align-items: baseline; font-size: 0.82rem;
  color: var(--muted); padding: 12px 0 16px; border-top: 1px solid var(--line); margin-top: 4px;
}}
.total-row b {{ font-family: 'IBM Plex Mono', monospace; font-size: 1.05rem; color: var(--ink); font-weight: 600; }}

/* ---------------------------------------------------------------------
   AI Designer: describe a change, the app updates the spec and rebuilds
   --------------------------------------------------------------------- */
.ai-designer {{ display: flex; flex-direction: column; min-height: 0; min-width: 0; padding-bottom: 18px; }}
.ai-designer-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
.ai-bot-reaction {{ width: 64px; height: 88px; flex: none; object-fit: contain; }}
.ai-designer-header h2 {{ margin: 0; }}
.ai-designer-header .sub {{ margin: 2px 0 0; }}
.ai-dot {{ width: 9px; height: 9px; border-radius: 50%; background: var(--accent); margin-top: 8px; flex: none; box-shadow: 0 0 0 3px var(--accent-light); }}
.ai-dot.is-busy {{ background: #e0a030; box-shadow: 0 0 0 3px rgba(224,160,48,0.18); animation: aiPulse 1s ease-in-out infinite; }}
.ai-dot.is-error {{ background: #e05a4a; box-shadow: 0 0 0 3px rgba(224,90,74,0.18); animation: none; }}
@keyframes aiPulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}
.ai-log {{ display: flex; flex: 1; min-height: 0; flex-direction: column; gap: 8px; overflow-y: auto; margin-bottom: 12px; }}
.ai-msg {{
  align-self: flex-start; max-width: 80%; padding: 9px 14px; border-radius: 12px; font-size: 0.85rem; line-height: 1.55;
  background: var(--panel-2); border: 1px solid var(--line); color: var(--ink); white-space: pre-wrap; overflow-wrap: anywhere;
}}
.ai-msg.user {{ align-self: flex-end; background: var(--accent-light); border-color: var(--accent); }}
.ai-msg.error {{ border-color: #a85142; color: #f0a58e; }}
.ai-suggestions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
.ai-suggestions button {{
  font: inherit; font-size: 0.78rem; padding: 7px 12px; border: 1px solid var(--line); border-radius: 999px;
  background: var(--panel-2); color: var(--muted); cursor: pointer;
}}
.ai-suggestions button:hover {{ border-color: var(--accent); color: var(--ink); }}
.ai-composer {{ display: flex; gap: 8px; }}
.ai-composer input {{
  flex: 1; min-width: 0; font: inherit; font-size: 0.88rem; padding: 11px 14px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--panel-2); color: var(--ink);
}}
.ai-composer input:focus {{ outline: none; border-color: var(--accent); }}
.ai-composer input:disabled {{ opacity: 0.6; }}
.ai-composer button {{
  display: grid; place-items: center; width: 42px; height: 42px; flex: none; border: none; border-radius: 10px;
  background: var(--accent); color: #ffffff; cursor: pointer;
}}
.ai-composer button:hover {{ background: var(--accent-hover); }}
.ai-composer button:disabled {{ opacity: 0.6; cursor: default; }}
.ai-composer svg {{ width: 17px; height: 17px; stroke: currentColor; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}

footer {{ margin-top: 18px; font-size: 0.75rem; color: var(--muted); }}
[hidden], .hidden {{ display: none !important; }}

/* ---------------------------------------------------------------------
   Generation overlay: shown while the AI Designer applies a change and
   rebuilds the model - same treatment as the main chat app's Generate flow.
   --------------------------------------------------------------------- */
.generation-overlay {{
  position: fixed; inset: 0; z-index: 40; display: flex; align-items: center; justify-content: center;
  padding: 24px; background: rgba(5,8,6,0.6); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
}}
.generation-overlay-card {{
  display: flex; flex-direction: column; align-items: center; text-align: center; width: min(420px, 100%);
  padding: 36px 32px; border: 1px solid var(--line); border-radius: 20px; background: var(--panel);
  box-shadow: 0 24px 80px rgba(0,0,0,0.4);
}}
.generation-overlay-card img {{ width: 128px; height: 128px; object-fit: contain; margin: 14px 0 6px; }}
.generation-overlay-card h1 {{ font-size: 1.3rem; font-weight: 700; letter-spacing: -0.02em; color: var(--ink); margin: 4px 0 8px; }}
.generation-overlay-card p {{ color: var(--muted); font-size: 0.85rem; line-height: 1.6; margin: 0; white-space: pre-wrap; }}
.generation-overlay.is-error .generation-overlay-card {{ border-color: #a85142; }}
.generation-overlay-close {{
  margin-top: 20px; font: inherit; font-size: 0.85rem; padding: 9px 18px; border: 1px solid var(--line);
  border-radius: 10px; background: var(--panel-2); color: var(--ink); cursor: pointer;
}}
.generation-overlay-close:hover {{ border-color: var(--accent); }}

.embedded body {{ background: #0c120e; }}
.embedded .app-shell {{ max-width: none; padding: 0; height: 100dvh; display: flex; flex-direction: column; }}
.embedded .viewer-topbar {{ border-radius: 0; margin-bottom: 0; }}
.embedded .viewer-body {{ flex: 1; min-height: 0; margin-top: 0; }}
.embedded .viewer-workspace {{ flex: 1; min-height: 0; height: auto; }}
.embedded .stage-card {{ height: 100%; border-radius: 0; border: 0; }}
.embedded .stage-wrap {{ height: 100%; }}

@media (max-width: 760px) {{
  .viewer-workspace {{ grid-template-columns: minmax(0, 1fr); height: auto; }}
  .ai-designer {{ height: 420px; }}
  .viewer-body {{ height: 65dvh; min-height: 360px; }}
}}

@page {{ size: A4 portrait; margin: 12mm; }}
@media print {{
  html, body {{ background: white !important; color: black; color-scheme: light; }}
  .viewer-topbar, .stage-wrap, footer, .ai-designer, .drawing-note, .generation-overlay {{ display: none !important; }}
  .app-shell, .viewer-workspace, .viewer-body, .stage-card {{ display: block !important; height: auto !important; min-height: 0 !important; max-width: none !important; overflow: visible !important; margin: 0 !important; padding: 0 !important; border: 0 !important; background: white !important; }}
  .plan-view, .plan-view[hidden] {{ display: block !important; height: auto; overflow: visible; padding: 0; }}
  .plan-diagram svg {{ width: 100%; height: 230mm; max-height: 230mm; }}
  .plan-heading {{ break-after: avoid; }}
  .plan-diagram {{ break-inside: avoid; }}
  .alignment-details {{ break-before: page; margin-top: 0; }}
  .alignment-table-wrap {{ overflow: visible; }}
  .alignment-details table {{ width: 100%; min-width: 0; table-layout: fixed; font-size: 9px; }}
  .alignment-details th, .alignment-details td {{ padding: 5px; overflow-wrap: anywhere; }}
  thead {{ display: table-header-group; }}
  tr {{ break-inside: avoid; }}
}}
</style>

<div class="app-shell">
  <header class="viewer-topbar">
    <button class="icon-btn" id="back-btn" title="Requirements chat" aria-label="Requirements chat">
      <svg viewBox="0 0 24 24"><path d="M19 12H5m0 0 7 7m-7-7 7-7"/></svg>
    </button>
    <div class="title-block">
      <h1>{title}</h1>
      <p class="subtitle">{len(rooms)} rooms &middot; {spec.num_bedrooms} BHK &middot; {entrance} entrance &middot; {style_notes}</p>
    </div>
    <div class="view-toolbar">

      <div class="toolbar-actions">
        <button id="view-model" aria-pressed="true" aria-controls="stage-wrap">3D Model</button>
        <button id="view-plan" aria-pressed="false" aria-controls="plan-view">2D Plan</button>
        <button id="print-plan" title="Print the 2D plan and alignment details">Print</button>
        <button id="reset-view">Reset Camera</button>
        <button id="top-view">Top View</button>
        <button id="fullscreen-btn" title="Fullscreen"><svg viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg></button>
      </div>
    </div>
    <div class="compass" title="North">N&uarr;</div>
  </header>

  <div class="viewer-workspace">
  <aside class="panel ai-designer" aria-label="AI Designer editing chat">
    <div class="ai-designer-header">
      <img class="ai-bot-reaction" id="ai-bot-reaction" src="/bot_reaction/Speaking.png?v=4" alt="AI Designer ready to help">
      <span class="ai-dot" id="ai-dot"></span>
      <div>
        <h2>AI Designer</h2>
        <p class="sub" id="ai-status">Ready to modify your home</p>
      </div>
    </div>
    <div class="ai-log" id="ai-log">
      <div class="ai-msg bot">Hi! I'm your AI home design assistant. Tell me what you'd like to change, and I'll rebuild the 3D model.</div>
    </div>
    <div class="ai-suggestions" id="ai-suggestions">
      <button type="button" data-prompt="Make the living room larger">Make the living room larger</button>
      <button type="button" data-prompt="Add a pooja room">Add a pooja room</button>
      <button type="button" data-prompt="Add a home office">Add a home office</button>
      <button type="button" data-prompt="Switch the entrance to east">Switch entrance to east</button>
    </div>
    <form class="ai-composer" id="ai-composer">
      <input id="ai-input" autocomplete="off" placeholder="Ask me to modify your home (e.g. &quot;make the kitchen bigger&quot;)">
      <button type="submit" id="ai-send" title="Send" aria-label="Send">
        <svg viewBox="0 0 24 24"><path d="M12 19V5m-6 6 6-6 6 6"/></svg>
      </button>
    </form>
  </aside>
  <div class="viewer-body">
    <div class="stage-card">
      <section class="plan-view" id="plan-view" hidden aria-label="2D floor plan and alignment details">
        <header class="plan-heading">
          <h2>{title} — Floor plan</h2>
          <p>{plot_w:g} × {plot_l:g} ft plot · {entrance} entrance · Revision {layout.version}</p>
        </header>
        <div class="plan-diagram">{plan_svg}</div>
        <section class="alignment-details">
          <h2>Room dimensions and alignment</h2>
          <p>{title} · Revision {layout.version} · {entrance} entrance · Total room-zone area: {total_area:,.1f} sq ft.</p>
          <p>North is up. Positions (X, Y) are measured in feet from the plot's northwest corner: X increases east and Y increases south. Floor 0 is the ground floor. Dimensions are east–west width × north–south length.</p>
          <div class="alignment-table-wrap"><table>
            <thead><tr><th>Room</th><th>Floor</th><th>Dimensions (ft)</th><th>Area (sq ft)</th><th>Position X, Y (ft)</th><th>Door / passage alignment</th><th>Windows</th></tr></thead>
            <tbody>{alignment_html}</tbody>
          </table></div>
          <p>Concept design. Dimensions describe room zones; do not scale the printout. Structural design, setbacks and construction clearances require separate review.</p>
        </section>
      </section>
      <div class="stage-wrap" id="stage-wrap">
        <canvas id="stage-canvas"></canvas>
        <div class="poster"><img src="data:image/png;base64,{poster_b64}" alt="Rendered 3D house model"></div>
        <div class="hud-caption" id="mv-status">Loading 3D viewer&hellip;</div>
        <div class="measurement-note" id="measurement-note" hidden>Measurements in feet. X = east and Y = south from the northwest plot corner. Room zones include wall thickness. Use Top View and zoom for a clearer view.</div>
        <div class="toggle-row">
          <span class="toggle-row-label">Show</span>
          <label class="chip"><input type="checkbox" id="toggle-labels" checked> Room labels</label>
          <label class="chip"><input type="checkbox" id="toggle-furniture" checked> Furniture</label>
          <label class="chip"><input type="checkbox" id="toggle-roof"> Roof</label>
          <label class="chip"><input type="checkbox" id="toggle-wireframe"> Wireframe</label>
          <label class="chip"><input type="checkbox" id="toggle-measurements"> Measurements</label>
        </div>
      </div>
      <p class="drawing-note">CONCEPT PLAN &nbsp;&middot;&nbsp; {plot_w:g}&prime; &times; {plot_l:g}&prime; plot &nbsp;&middot;&nbsp; Dimensions in feet &nbsp;&middot;&nbsp; Rev {layout.version:02d}</p>
    </div>


  </div>
  </div>

  <footer>Concept design &middot; Revision {layout.version}. Room areas are rectangular zone areas. Structural design, setbacks and construction clearances require separate review.</footer>

  <div class="generation-overlay hidden" id="generation-overlay" role="status" aria-live="polite">
    <div class="generation-overlay-card">
      <span class="eyebrow">CONSTRUCTIQ</span>
      <img id="generation-overlay-img" src="/bot_reaction/working.png?v=4" alt="">
      <h1 id="generation-overlay-title">Applying your changes&hellip;</h1>
      <p id="generation-overlay-message">Updating your requirements&hellip;</p>
      <button class="generation-overlay-close hidden" id="generation-overlay-close" type="button">Close</button>
    </div>
  </div>
</div>

<script>

function selectDrawingView(model) {{
  document.getElementById('stage-wrap').hidden = !model;
  document.getElementById('plan-view').hidden = model;
  document.getElementById('view-model').setAttribute('aria-pressed', String(model));
  document.getElementById('view-plan').setAttribute('aria-pressed', String(!model));
  document.getElementById('reset-view').disabled = !model;
  document.getElementById('top-view').disabled = !model;
}}
document.getElementById('view-model').onclick = () => selectDrawingView(true);
document.getElementById('view-plan').onclick = () => selectDrawingView(false);
document.getElementById('print-plan').onclick = () => window.print();
document.getElementById('back-btn').onclick = () => {{
  window.location.href = '/';
}};
document.getElementById('fullscreen-btn').onclick = () => {{
  const el = document.getElementById('plan-view').hidden ? document.getElementById('stage-wrap') : document.getElementById('plan-view');
  if (!document.fullscreenElement) el.requestFullscreen?.();
  else document.exitFullscreen?.();
}};
window.setTimeout(() => {{
  if (!document.getElementById('stage-wrap').classList.contains('is-ready'))
    document.getElementById('mv-status').textContent = '3D unavailable or still loading. 3D requires internet and WebGL. Your rendered preview is shown while it loads.';
}}, 15000);

/* -----------------------------------------------------------------------
   AI Designer: describes a change -> agent.spec_agent updates the
   specification -> agent.layout_agent/layout_solver rebuild the layout ->
   this page navigates to the newly built (versioned) viewer. Same two
   endpoints the main chat app itself uses, called from this page because
   it's served by that same backend (same origin).
   ----------------------------------------------------------------------- */
(function() {{
  const sessionId = (location.pathname.match(/\\/sessions\\/([^/]+)\\/assets\\//) || [])[1];
  const log = document.getElementById('ai-log');
  const form = document.getElementById('ai-composer');
  const input = document.getElementById('ai-input');
  const sendBtn = document.getElementById('ai-send');
  const statusEl = document.getElementById('ai-status');
  const dot = document.getElementById('ai-dot');
  const botImage = document.getElementById('ai-bot-reaction');
  const reactions = {{
    ready: ['Speaking.png', 'AI Designer ready to help'],
    listening: ['listning.png', 'AI Designer listening'],
    working: ['working.png', 'AI Designer working on your home'],
    speaking: ['Speaking.png', 'AI Designer responding'],
    error: ['error.png', 'AI Designer needs your attention'],
  }};
  Object.values(reactions).forEach(([file]) => {{
    const image = new Image();
    image.src = `/bot_reaction/${{file}}?v=4`;
  }});
  let listeningTimer;
  function setReaction(name) {{
    const [file, label] = reactions[name];
    botImage.src = `/bot_reaction/${{file}}?v=4`;
    botImage.alt = label;
  }}
  function showListening() {{
    if (busy) return;
    window.clearTimeout(listeningTimer);
    setReaction('listening');
    listeningTimer = window.setTimeout(() => {{ if (!busy) setReaction('ready'); }}, 1600);
  }}
  const overlay = document.getElementById('generation-overlay');
  const overlayImg = document.getElementById('generation-overlay-img');
  const overlayTitle = document.getElementById('generation-overlay-title');
  const overlayMessage = document.getElementById('generation-overlay-message');
  const overlayClose = document.getElementById('generation-overlay-close');
  let busy = false;

  function addAiMessage(role, text) {{
    const el = document.createElement('div');
    el.className = 'ai-msg' + (role === 'user' ? ' user' : role === 'error' ? ' error' : ' bot');
    el.textContent = text;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
  }}

  function setBusy(isBusy, statusText, isError) {{
    window.clearTimeout(listeningTimer);
    busy = isBusy;
    input.disabled = isBusy;
    sendBtn.disabled = isBusy;
    dot.classList.toggle('is-busy', isBusy);
    dot.classList.toggle('is-error', !!isError);
    if (statusText) statusEl.textContent = statusText;
    setReaction(isError ? 'error' : isBusy ? 'working' : 'ready');
  }}

  function showOverlay(title, message) {{
    overlay.classList.remove('hidden', 'is-error');
    overlayImg.src = '/bot_reaction/working.png?v=4';
    overlayClose.classList.add('hidden');
    overlayTitle.textContent = title;
    overlayMessage.textContent = message;
  }}
  function updateOverlay(title, message) {{
    overlayTitle.textContent = title;
    overlayMessage.textContent = message;
  }}
  function showOverlayError(message) {{
    overlay.classList.remove('hidden');
    overlay.classList.add('is-error');
    overlayImg.src = '/bot_reaction/error.png?v=4';
    overlayTitle.textContent = 'Something needs attention';
    overlayMessage.textContent = message;
    overlayClose.classList.remove('hidden');
  }}
  function hideOverlay() {{
    overlay.classList.add('hidden');
    overlay.classList.remove('is-error');
  }}
  overlayClose.addEventListener('click', hideOverlay);

  async function requestJson(path, body) {{
    const res = await fetch(path, {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(body) }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok) {{
      const detail = typeof data.detail === 'string' ? data.detail : data.detail?.issues?.join('\\n');
      throw new Error(detail || 'The design service could not complete that request.');
    }}
    return data;
  }}

  async function submitChange(text) {{
    addAiMessage('user', text);
    setBusy(true, 'Updating your requirements\\u2026');
    showOverlay('Applying your changes\\u2026', 'Updating your requirements\\u2026');
    try {{
      const chatResult = await requestJson(`/sessions/${{sessionId}}/messages`, {{ message: text, auto_generate: true }});
      addAiMessage('bot', chatResult.reply);
      setBusy(true, 'Rebuilding your 3D home\\u2026 this can take a few minutes.');
      updateOverlay('Rebuilding your 3D home\\u2026', 'This can take a few minutes while we redesign and re-render your model.');
      const genResult = await requestJson(`/sessions/${{sessionId}}/generate`, {{ edit_request: text, build_3d: true }});
      const buildFailure = (genResult.warnings || []).find(message => message.startsWith('3D model build failed:'));
      if (buildFailure) throw new Error(buildFailure);
      const viewer = genResult.viewer_url;
      if (!viewer) throw new Error('The plan was updated but the 3D viewer was not returned.');
      const versioned = new URL(viewer, window.location.href);
      versioned.searchParams.set('v', String(genResult.session.layout.version));
      addAiMessage('bot', 'Updated! Loading the new model\\u2026');
      setBusy(true, 'Loading the updated model\\u2026');
      updateOverlay('Your home is ready\\u2026', 'Loading the updated model\\u2026');
      setReaction('speaking');
      window.setTimeout(() => {{ window.location.href = versioned.href; }}, 500);
    }} catch (err) {{
      addAiMessage('error', err.message);
      setBusy(false, 'Design needs an adjustment', true);
      showOverlayError(err.message);
      window.setTimeout(() => {{ if (!busy) setBusy(false, 'Ready to modify your home'); }}, 4000);
    }}
  }}

  if (!sessionId) {{
    setBusy(true, 'Open this model through the ConstructIQ app to make changes here.', true);
  }} else {{
    input.addEventListener('input', showListening);
    input.addEventListener('focus', showListening);
    form.addEventListener('submit', (e) => {{
      e.preventDefault();
      const text = input.value.trim();
      if (!text || busy) return;
      input.value = '';
      submitChange(text);
    }});
    document.getElementById('ai-suggestions').addEventListener('click', (e) => {{
      const btn = e.target.closest('button[data-prompt]');
      if (!btn || busy) return;
      input.value = btn.dataset.prompt;
      input.focus();
      showListening();
    }});
  }}
}})();
</script>

<script id="glb-data" type="text/plain">{glb_b64}</script>
<script id="rooms-data" type="application/json">{furnish_rooms}</script>
<script type="importmap">
{{
  "imports": {{
    "three": "https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/"
  }}
}}
</script>
<script type="module">
import * as THREE from 'three';
import {{ GLTFLoader }} from 'three/addons/loaders/GLTFLoader.js';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';

const status = document.getElementById('mv-status');
const wrap = document.getElementById('stage-wrap');
const canvas = document.getElementById('stage-canvas');

function setStatus(text, isError) {{
  status.textContent = text;
  status.classList.toggle('is-error', !!isError);
}}

function base64ToArrayBuffer(b64) {{
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}}

const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true }});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x05070a);
scene.fog = new THREE.Fog(0x05070a, 60, 320);
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 2000);

const grid = new THREE.GridHelper(300, 60, 0x1d2b21, 0x121a15);
grid.position.y = -0.25;
scene.add(grid);

scene.add(new THREE.HemisphereLight(0xfdf6ec, 0x1a201c, 1.1));
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(60, 90, 40);
scene.add(sun);
scene.add(new THREE.AmbientLight(0xffffff, 0.35));

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 5;
controls.maxDistance = 400;

let sceneCenter = new THREE.Vector3();
let sceneRadius = 40;

function resize() {{
  const w = wrap.clientWidth, h = wrap.clientHeight;
  if (!w || !h) return;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h, false);
}}
new ResizeObserver(resize).observe(wrap);
resize();

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}}
animate();

document.getElementById('top-view').onclick = () => {{
  camera.position.set(sceneCenter.x, sceneCenter.y + sceneRadius * 1.7, sceneCenter.z + 0.01);
  controls.target.copy(sceneCenter);
  controls.update();
}};

function addFurnitureBox(group, cx, cz, sizeX, sizeZ, height, color, yBase) {{
  const geo = new THREE.BoxGeometry(sizeX, height, sizeZ);
  const mat = new THREE.MeshStandardMaterial({{ color, roughness: 0.8, metalness: 0.05 }});
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(cx, yBase + height / 2, cz);
  group.add(mesh);
}}

function makeLabelSprite(text, dotColor, worldHeight = 3.4) {{
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  const fontSize = 44;
  const font = `600 ${{fontSize}}px 'IBM Plex Sans', sans-serif`;
  ctx.font = font;
  const dotR = 9, gap = 16, padX = 26, padY = 16;
  const textW = ctx.measureText(text).width;
  c.width = Math.ceil(padX * 2 + dotR * 2 + gap + textW);
  c.height = fontSize + padY * 2;
  ctx.font = font;
  ctx.fillStyle = 'rgba(17,26,20,0.86)';
  ctx.beginPath();
  ctx.roundRect(0, 0, c.width, c.height, c.height / 2);
  ctx.fill();
  ctx.fillStyle = dotColor || '#8b98a3';
  ctx.beginPath();
  ctx.arc(padX + dotR, c.height / 2, dotR, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#edf2ee';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, padX + dotR * 2 + gap, c.height / 2 + 2);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({{ map: texture, depthTest: false, depthWrite: false, transparent: true }}));
  sprite.scale.set(worldHeight * (c.width / c.height), worldHeight, 1);
  sprite.renderOrder = 999;
  return sprite;
}}

// All dimension endpoints use the same feet-based room geometry as the model.
function measurementData(roomsData, plotWidth, plotLength) {{
  const dimensions = [];
  const positions = [];
  const seenOpenings = new Set();
  const feet = value => `${{Number(value.toFixed(2))}} ft`;
  const add = (a, b, floor, label, color) => dimensions.push({{ a, b, floor, label, color }});
  const floors = [...new Set(roomsData.map(room => room.floor))];
  floors.forEach(floor => {{
    add([0, -3], [plotWidth, -3], floor, `Plot E–W: ${{feet(plotWidth)}}`, '#d4d4d4');
    add([-3, 0], [-3, plotLength], floor, `Plot N–S: ${{feet(plotLength)}}`, '#d4d4d4');
  }});
  roomsData.forEach(room => {{
    const x = room.x, y = room.y, w = room.width, l = room.length;
    add([x, y + l * .22], [x + w, y + l * .22], room.floor, `W ${{feet(w)}}`, '#d4d4d4');
    add([x + w * .22, y], [x + w * .22, y + l], room.floor, `L ${{feet(l)}}`, '#d4d4d4');
    positions.push({{ x: x + w / 2, y: y + l * .75, floor: room.floor,
      text: `X ${{Number(x.toFixed(2))}}, Y ${{Number(y.toFixed(2))}} ft` }});
    const openings = [...(room.doors || []).map(item => ({{ ...item, type: item.kind === 'opening' ? 'Passage' : 'Door' }})),
                      ...(room.windows || []).map(item => ({{ ...item, type: 'Window' }}))];
    openings.forEach(opening => {{
      const horizontal = opening.wall === 'north' || opening.wall === 'south';
      const center = (horizontal ? x : y) + opening.position * (horizontal ? w : l);
      const fixed = horizontal ? y + (opening.wall === 'south' ? l : 0) : x + (opening.wall === 'east' ? w : 0);
      const lo = center - opening.width / 2, hi = center + opening.width / 2;
      const a = horizontal ? [lo, fixed] : [fixed, lo];
      const b = horizontal ? [hi, fixed] : [fixed, hi];
      const key = JSON.stringify([room.floor, opening.type, ...a, ...b].map(value => typeof value === 'number' ? Number(value.toFixed(5)) : value));
      if (seenOpenings.has(key)) return;
      seenOpenings.add(key);
      add(a, b, room.floor, `${{opening.type}} ${{feet(opening.width)}}`, opening.type === 'Window' ? '#2e8b57' : '#b7f0c9');
    }});
  }});
  return {{ dimensions, positions }};
}}

function buildMeasurements(roomsData) {{
  const group = new THREE.Group();
  const data = measurementData(roomsData, {plot_w}, {plot_l});
  const materials = new Map();
  data.dimensions.forEach(item => {{
    const height = item.floor * 9.3 + 10.8;
    const a = new THREE.Vector3(item.a[0], height, item.a[1]);
    const b = new THREE.Vector3(item.b[0], height, item.b[1]);
    const perpendicular = new THREE.Vector3(-(b.z - a.z), 0, b.x - a.x).normalize().multiplyScalar(.35);
    const points = [a, b, a.clone().sub(perpendicular), a.clone().add(perpendicular), b.clone().sub(perpendicular), b.clone().add(perpendicular)];
    if (!materials.has(item.color)) materials.set(item.color, new THREE.LineBasicMaterial({{ color: item.color, depthTest: false, depthWrite: false, transparent: true, opacity: .9 }}));
    const line = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points), materials.get(item.color));
    line.renderOrder = 1000;
    group.add(line);
    const label = makeLabelSprite(item.label, item.color, 1.05);
    label.position.copy(a).lerp(b, .5);
    label.position.y += .5;
    label.renderOrder = 1001;
    group.add(label);
  }});
  data.positions.forEach(item => {{
    const label = makeLabelSprite(item.text, '#d4d4d4', 1.0);
    label.position.set(item.x, item.floor * 9.3 + 11.3, item.y);
    label.renderOrder = 1001;
    group.add(label);
  }});
  return group;
}}

const FURNITURE_SPECS = {{
  bedroom: [
    {{ fx: 0.28, fz: 0.32, fw: 0.42, fl: 0.55, minW: 5.5, maxW: 6.5, minL: 6, maxL: 7, h: 1.6, color: 0x8a6a55 }},
    {{ fx: 0.88, fz: 0.5, fw: 0.16, fl: 0.5, minW: 1.3, maxW: 1.8, minL: 3, maxL: 5, h: 6.0, color: 0x4d3c30 }},
  ],
  bathroom: [
    {{ fx: 0.22, fz: 0.22, fw: 0.28, fl: 0.34, minW: 1.1, maxW: 1.5, minL: 1.7, maxL: 2.2, h: 1.3, color: 0xeef1ee }},
    {{ fx: 0.22, fz: 0.78, fw: 0.3, fl: 0.24, minW: 1.3, maxW: 1.8, minL: 1.3, maxL: 1.8, h: 2.6, color: 0xd7dadb }},
  ],
  kitchen: [
    {{ fx: 0.5, fz: 0.14, fw: 0.86, fl: 0.2, minW: 4, maxW: 11, minL: 1.8, maxL: 2.4, h: 2.9, color: 0x33393e }},
    {{ fx: 0.86, fz: 0.82, fw: 0.22, fl: 0.22, minW: 2.2, maxW: 2.6, minL: 2.2, maxL: 2.6, h: 5.5, color: 0xd7dadb }},
  ],
  dining: [
    {{ fx: 0.5, fz: 0.5, fw: 0.42, fl: 0.42, minW: 4, maxW: 6, minL: 4, maxL: 6, h: 2.4, color: 0x6b4f3a }},
  ],
  living: [
    {{ fx: 0.5, fz: 0.16, fw: 0.55, fl: 0.18, minW: 6, maxW: 11, minL: 2.4, maxL: 3, h: 2.6, color: 0x54636b }},
    {{ fx: 0.5, fz: 0.5, fw: 0.22, fl: 0.28, minW: 2.5, maxW: 4, minL: 2, maxL: 3.2, h: 1.3, color: 0x7a5c3e }},
  ],
  pooja: [
    {{ fx: 0.5, fz: 0.2, fw: 0.5, fl: 0.32, minW: 2.5, maxW: 4.5, minL: 1.5, maxL: 2.5, h: 3.2, color: 0xc79a45 }},
  ],
  office: [
    {{ fx: 0.5, fz: 0.16, fw: 0.55, fl: 0.2, minW: 3.5, maxW: 5.5, minL: 1.8, maxL: 2.4, h: 2.4, color: 0x5a4632 }},
    {{ fx: 0.5, fz: 0.34, fw: 0.16, fl: 0.16, minW: 1.3, maxW: 1.6, minL: 1.3, maxL: 1.6, h: 2.6, color: 0x33393e }},
  ],
  car_parking: [
    {{ fx: 0.5, fz: 0.5, fw: 0.78, fl: 0.86, minW: 6, maxW: 9, minL: 13, maxL: 17, h: 4.2, color: 0x8f9aa3 }},
  ],
  bike_parking: [
    {{ fx: 0.32, fz: 0.5, fw: 0.24, fl: 0.62, minW: 1.6, maxW: 2.2, minL: 5, maxL: 8, h: 3.2, color: 0x232a30 }},
    {{ fx: 0.68, fz: 0.5, fw: 0.24, fl: 0.62, minW: 1.6, maxW: 2.2, minL: 5, maxL: 8, h: 3.2, color: 0x232a30 }},
  ],
}};

const CATEGORY_DOT_COLORS = {json.dumps(CATEGORY_COLORS)};
const DEFAULT_DOT_COLOR = {json.dumps(DEFAULT_CATEGORY_COLOR)};

function clampFt(v, lo, hi) {{ return Math.min(hi, Math.max(lo, v)); }}

function buildFurniture(roomsData, furnitureGroup, labelGroup) {{
  roomsData.forEach((r) => {{
    const specs = FURNITURE_SPECS[r.category];
    if (specs) {{
      specs.forEach((s) => {{
        const sizeX = Math.min(r.width * .8, clampFt(r.width * s.fw, s.minW, s.maxW));
        const sizeZ = Math.min(r.length * .8, clampFt(r.length * s.fl, s.minL, s.maxL));
        const px = r.x + clampFt(r.width * s.fx, sizeX / 2 + r.width * .05, r.width * .95 - sizeX / 2);
        const pz = r.y + clampFt(r.length * s.fz, sizeZ / 2 + r.length * .05, r.length * .95 - sizeZ / 2);
        addFurnitureBox(furnitureGroup, px, pz, sizeX, sizeZ, s.h, s.color, r.floor * 9.3);
      }});
    }}
    const dotColor = CATEGORY_DOT_COLORS[r.category] || DEFAULT_DOT_COLOR;
    const label = makeLabelSprite(r.name, dotColor);
    label.position.set(r.x + r.width / 2, r.floor * 9.3 + 10.2, r.y + r.length / 2);
    labelGroup.add(label);
  }});
}}

try {{
  const b64 = document.getElementById('glb-data').textContent;
  const buffer = base64ToArrayBuffer(b64);
  new GLTFLoader().parse(
    buffer,
    '',
    (gltf) => {{
      scene.add(gltf.scene);
      const box = new THREE.Box3().setFromObject(gltf.scene);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const radius = Math.max(size.x, size.y, size.z);
      sceneCenter = center;
      sceneRadius = radius;
      controls.target.copy(center);
      camera.position.set(center.x + radius * 0.9, center.y + radius * 0.7, center.z + radius * 0.9);
      camera.near = radius / 100;
      camera.far = radius * 20;
      camera.updateProjectionMatrix();
      controls.update();
      controls.saveState();
      document.getElementById('reset-view').onclick = () => controls.reset();
      wrap.classList.add('is-ready');
      setStatus('Loaded â€” drag to orbit', false);

      const roofParts = [];
      const solidMaterials = [];
      gltf.scene.traverse((obj) => {{
        if (obj.name && obj.name.toLowerCase().startsWith('roof')) roofParts.push(obj);
        if (obj.isMesh && obj.material && !solidMaterials.includes(obj.material)) solidMaterials.push(obj.material);
      }});
      roofParts.forEach((r) => {{ r.visible = false; }});

      const roofToggle = document.getElementById('toggle-roof');
      const wireToggle = document.getElementById('toggle-wireframe');
      roofToggle.addEventListener('change', () => {{ roofParts.forEach((r) => {{ r.visible = roofToggle.checked; }}); }});
      wireToggle.addEventListener('change', () => {{ solidMaterials.forEach((m) => {{ m.wireframe = wireToggle.checked; }}); }});

      const roomsData = JSON.parse(document.getElementById('rooms-data').textContent);
      const furnitureGroup = new THREE.Group();
      const labelGroup = new THREE.Group();
      buildFurniture(roomsData, furnitureGroup, labelGroup);
      scene.add(furnitureGroup);
      scene.add(labelGroup);
      const measurementGroup = buildMeasurements(roomsData);
      scene.add(measurementGroup);
      const measurementToggle = document.getElementById('toggle-measurements');
      const updateMeasurements = () => {{
        measurementGroup.visible = measurementToggle.checked;
        document.getElementById('measurement-note').hidden = !measurementToggle.checked;
      }};
      updateMeasurements();
      measurementToggle.addEventListener('change', updateMeasurements);

      const furnitureToggle = document.getElementById('toggle-furniture');
      const labelsToggle = document.getElementById('toggle-labels');
      furnitureGroup.visible = furnitureToggle.checked;
      labelGroup.visible = labelsToggle.checked;
      furnitureToggle.addEventListener('change', () => {{ furnitureGroup.visible = furnitureToggle.checked; }});
      labelsToggle.addEventListener('change', () => {{ labelGroup.visible = labelsToggle.checked; }});
    }},
    (err) => {{
      console.error('GLTF parse error', err);
      setStatus('Load error: ' + (err && err.message ? err.message : 'could not parse model'), true);
    }}
  );
}} catch (err) {{
  console.error('failed to parse model buffer', err);
  setStatus('Load error: ' + err.message, true);
}}
</script>
"""
