"""Scale-accurate SVG floor plans with one shared wall and opening geometry."""
from __future__ import annotations
from html import escape
import textwrap
from agent.schemas import Door, Layout, Room
from agent.openings import EPS, WALLS, opening_span, subtract_intervals, wall_span

SCALE = 24
PADDING = 94
WALL_STROKE = "#303534"


def render_png(layout: Layout, **options) -> bytes:
    from resvg_py import svg_to_bytes
    return svg_to_bytes(svg_string=render_svg(layout, **options))

def _line(x1, y1, x2, y2, stroke=WALL_STROKE, width=1, extra=""):
    return (f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" '
            f'stroke="{stroke}" stroke-width="{width:g}" {extra}/>')

def _text(x, y, value, size=13, extra=""):
    return (f'<text x="{x:g}" y="{y:g}" font-size="{size}" text-anchor="middle" '
            f'fill="#28302e" {extra}>{escape(str(value))}</text>')

def _wall_geometry(rooms):
    """Union collinear room edges, then subtract canonical door/window intervals."""
    groups = {}
    def key_for(axis, fixed):
        return next((key for key in groups if key[0] == axis and abs(key[1] - fixed) <= EPS),
                    (axis, fixed))
    for room in rooms:
        for wall in WALLS:
            axis, fixed, start, end = wall_span(room, wall)
            key = key_for(axis, fixed)
            groups.setdefault(key, {"edges": [], "cuts": []})["edges"].append((start, end))
    for room in rooms:
        for opening in [*room.doors, *room.windows]:
            axis, fixed, start, end = opening_span(room, opening)
            groups[key_for(axis, fixed)]["cuts"].append((start, end))
    for (axis, fixed), group in sorted(groups.items()):
        merged = []
        for start, end in sorted(group["edges"]):
            if merged and start <= merged[-1][1] + EPS:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        for start, end in merged:
            for lo, hi in subtract_intervals(start, end, group["cuts"]):
                yield axis, fixed, lo, hi

def _door_svg(room: Room, door: Door, ox: float, oy: float) -> str:
    axis, fixed, lo, hi = opening_span(room, door)
    w = (hi - lo) * SCALE
    if axis == "h":
        hx, hy = ox + lo * SCALE, oy + fixed * SCALE
        ex, ey = hx + w, hy
        lx, ly = hx, hy + (w if door.wall == "north" else -w)
        sweep = 0 if door.wall == "north" else 1
    else:
        hx, hy = ox + fixed * SCALE, oy + lo * SCALE
        ex, ey = hx, hy + w
        lx, ly = hx + (w if door.wall == "west" else -w), hy
        sweep = 1 if door.wall == "west" else 0
    return (f'<g class="door">'
            f'<path d="M {lx:g} {ly:g} A {w:g} {w:g} 0 0 {sweep} {ex:g} {ey:g}" '
            f'fill="none" stroke="#7d8581" stroke-width="0.9"/>'
            + _line(hx, hy, lx, ly, width=1.5)
            + f'<circle cx="{hx:g}" cy="{hy:g}" r="1.5" fill="{WALL_STROKE}"/></g>')

def _dimensions(layout, ox, oy):
    w, h = layout.plot_width_ft * SCALE, layout.plot_length_ft * SCALE
    parts = []
    for y, edge in ((oy - 35, oy), (oy + h + 35, oy + h)):
        parts += [_line(ox, edge, ox, y - 7, stroke="#9ba39f"),
                  _line(ox + w, edge, ox + w, y - 7, stroke="#9ba39f"),
                  _line(ox, y, ox + w, y, extra='marker-start="url(#dim)" marker-end="url(#dim)"'),
                  f'<rect x="{ox+w/2-35:g}" y="{y-9:g}" width="70" height="18" fill="white"/>',
                  _text(ox + w / 2, y + 4, f"{layout.plot_width_ft:g} ft", 12)]
    for x, edge in ((ox - 35, ox), (ox + w + 35, ox + w)):
        parts += [_line(edge, oy, x - 7, oy, stroke="#9ba39f"),
                  _line(edge, oy + h, x - 7, oy + h, stroke="#9ba39f"),
                  _line(x, oy, x, oy + h, extra='marker-start="url(#dim)" marker-end="url(#dim)"'),
                  f'<rect x="{x-9:g}" y="{oy+h/2-35:g}" width="18" height="70" fill="white"/>',
                  _text(x, oy + h / 2 + 4, f"{layout.plot_length_ft:g} ft", 12,
                        extra=f'transform="rotate(-90 {x:g} {oy+h/2:g})"')]
    return "".join(parts)


def _label_center(room, lines):
    """Prefer the center; shift labels away from door swing envelopes when needed."""
    half_w = max(max((len(line) for line in lines), default=0) * 3.7,
                 len(f"{room.width:g} x {room.length:g} ft") * 3) / SCALE
    half_h = (len(lines) * 16 + 18) / (2 * SCALE)
    blocked = []
    for door in room.doors:
        if door.kind != "door":
            continue
        axis, fixed, lo, hi = opening_span(room, door)
        if axis == "h":
            ymin, ymax = (fixed, fixed + door.width) if door.wall == "north" else (fixed-door.width, fixed)
            blocked.append((lo, ymin, hi, ymax))
        else:
            xmin, xmax = (fixed, fixed + door.width) if door.wall == "west" else (fixed-door.width, fixed)
            blocked.append((xmin, lo, xmax, hi))
    for fx, fy in ((.5,.5), (.5,.7), (.5,.3), (.65,.5), (.35,.5), (.5,.8), (.5,.2)):
        x, y = room.x+room.width*fx, room.y+room.length*fy
        if (x-half_w < room.x+.2 or x+half_w > room.x+room.width-.2
                or y-half_h < room.y+.2 or y+half_h > room.y+room.length-.2):
            continue
        if not any(x+half_w > x1 and x-half_w < x2 and y+half_h > y1 and y-half_h < y2
                   for x1,y1,x2,y2 in blocked):
            return x,y
    return room.x+room.width/2, room.y+room.length/2

def render_svg(layout: Layout, wall_style: str = "double", show_dimensions: bool = True,
               show_grid: bool = False, show_labels: bool = True) -> str:
    if wall_style not in ("single", "double"):
        raise ValueError("wall_style must be single or double")
    floors = sorted({room.floor for room in layout.rooms}) or [0]
    plot_w, plot_h = layout.plot_width_ft * SCALE, layout.plot_length_ft * SCALE
    panel_w, panel_h = plot_w + 2 * PADDING, plot_h + 2 * PADDING + 90
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{panel_w:g}" '
             f'height="{panel_h*len(floors):g}" viewBox="0 0 {panel_w:g} {panel_h*len(floors):g}" '
             f'data-scale="{SCALE:g}" font-family="Arial, sans-serif">',
             '<title>ConstructIQ floor plan</title>',
             '<defs><marker id="dim" viewBox="0 0 10 10" refX="5" refY="5" '
             'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
             '<path d="M 9 1 L 1 5 L 9 9" fill="none" stroke="#68716b"/></marker></defs>',
             f'<rect width="100%" height="100%" fill="white"/>']
    for index, floor in enumerate(floors):
        ox, oy = PADDING, index * panel_h + PADDING + 20
        rooms = [room for room in layout.rooms if room.floor == floor]
        parts.append(f'<g class="floor" data-floor="{floor}">')
        parts.append(_text(panel_w / 2, index * panel_h + 27,
                           f"GROUND FLOOR  /  {layout.plot_width_ft:g} x {layout.plot_length_ft:g} ft" if floor == 0
                           else f"FLOOR {floor}  /  {layout.plot_width_ft:g} x {layout.plot_length_ft:g} ft", 12,
                           'letter-spacing="2"'))
        parts.append(f'<rect x="{ox-12:g}" y="{oy-12:g}" width="{plot_w+24:g}" height="{plot_h+24:g}" '
                     'fill="none" stroke="#afb6b1" stroke-dasharray="10 5"/>')
        if show_grid:
            for x in range(1, int(layout.plot_width_ft)):
                parts.append(_line(ox + x*SCALE, oy, ox + x*SCALE, oy+plot_h, stroke="#f0f2f0", width=.5))
            for y in range(1, int(layout.plot_length_ft)):
                parts.append(_line(ox, oy + y*SCALE, ox+plot_w, oy + y*SCALE, stroke="#f0f2f0", width=.5))
        parts.append('<g class="walls">')
        for axis, fixed, lo, hi in _wall_geometry(rooms):
            coords = ((ox+lo*SCALE, oy+fixed*SCALE, ox+hi*SCALE, oy+fixed*SCALE) if axis == "h"
                      else (ox+fixed*SCALE, oy+lo*SCALE, ox+fixed*SCALE, oy+hi*SCALE))
            parts.append(_line(*coords, width=6 if wall_style == "double" else 3))
            if wall_style == "double":
                parts.append(_line(*coords, stroke="#fbfbf7", width=3.6))
        parts.append('</g>')
        # A physical interior door is drawn once, preferably swinging into its private room.
        seen = set()
        for room in sorted(rooms, key=lambda r: (any(k in r.name.lower() for k in ("living", "dining", "hall", "passage", "corridor")), r.id)):
            for door in room.doors:
                if door.kind == "opening":
                    continue
                axis, fixed, lo, hi = opening_span(room, door)
                key = (tuple(sorted((room.id, door.connects_to or ""))), axis,
                       round(fixed, 3), round(lo, 3), round(hi, 3))
                if key not in seen:
                    parts.append(_door_svg(room, door, ox, oy))
                    seen.add(key)
            for window in room.windows:
                axis, fixed, lo, hi = opening_span(room, window)
                parts.append('<g class="window">')
                for offset in (-2.8, 0, 2.8):
                    coords = ((ox+lo*SCALE, oy+fixed*SCALE+offset, ox+hi*SCALE, oy+fixed*SCALE+offset) if axis == "h"
                              else (ox+fixed*SCALE+offset, oy+lo*SCALE, ox+fixed*SCALE+offset, oy+hi*SCALE))
                    parts.append(_line(*coords, stroke="#65776d", width=.8))
                parts.append('</g>')
        parts.append('<g class="room-edit-targets">')
        for room in rooms:
            rx, ry = ox + room.x * SCALE, oy + room.y * SCALE
            rw, rh = room.width * SCALE, room.length * SCALE
            parts.append(f'<rect class="room-hit" data-room="{escape(room.id, quote=True)}" '
                        f'data-x="{room.x:g}" data-y="{room.y:g}" data-width="{room.width:g}" '
                        f'data-length="{room.length:g}" x="{rx:g}" y="{ry:g}" width="{rw:g}" '
                        f'height="{rh:g}" fill="transparent"/>')
            strip = 10
            edges = (("north", rx, ry - strip/2, rw, strip), ("south", rx, ry+rh - strip/2, rw, strip),
                    ("west", rx - strip/2, ry, strip, rh), ("east", rx+rw - strip/2, ry, strip, rh))
            for edge, ex, ey, ew, eh in edges:
                parts.append(f'<rect class="room-edge" data-room="{escape(room.id, quote=True)}" '
                            f'data-edge="{edge}" x="{ex:g}" y="{ey:g}" width="{ew:g}" height="{eh:g}" '
                            'fill="transparent"/>')
        parts.append('</g>')
        if show_labels:
            for room in rooms:
                lines = textwrap.wrap(room.name, width=max(8, int(room.width*SCALE/8)-2))
                label_x, label_y_ft = _label_center(room, lines)
                cx, cy = ox+label_x*SCALE, oy+label_y_ft*SCALE
                label_y = cy - (len(lines)-1)*8 - 4
                parts.append(f'<g class="room-label" data-room="{escape(room.id, quote=True)}">')
                for i, line in enumerate(lines):
                    parts.append(_text(cx, label_y+i*16, line, 14))
                parts.append(_text(cx, label_y+len(lines)*16+2, f"{room.width:g} x {room.length:g} ft", 12))
                parts.append('</g>')
        if show_dimensions:
            parts.append(_dimensions(layout, ox, oy))
        parts.append(_text(panel_w-24, index*panel_h+36, "N", 12))
        parts.append(_line(panel_w-24, index*panel_h+59, panel_w-24, index*panel_h+42,
                           extra='marker-end="url(#dim)"'))
        footer_y = (index+1)*panel_h-64
        parts.append(_line(24, footer_y, panel_w-24, footer_y, width=1.5))
        parts.append(_text(34, footer_y+22, "CONSTRUCTIQ / RESIDENTIAL", 12,
                           'style="text-anchor:start;font-weight:bold;letter-spacing:1px"'))
        parts.append(_text(34, footer_y+42, "CONCEPT PLAN / Dimensions in feet / Do not scale printed drawing", 10,
                           'style="text-anchor:start"'))
        parts.append(_text(panel_w-34, footer_y+22, f"A-{index+1:02d} / REV {layout.version:02d}", 12,
                           'style="text-anchor:end;font-weight:bold"'))
        parts.append(_text(panel_w-34, footer_y+42,
                           f"Plot area {layout.plot_width_ft*layout.plot_length_ft:,.0f} sq ft", 10,
                           'style="text-anchor:end"'))
        parts.append('</g>')
    parts.append('</svg>')
    return "\n".join(parts)

