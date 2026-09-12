"""Pure-Python 3D scene data derived from a Layout â€” no Blender/bpy dependency,
so the geometry can be tested standalone before ever touching Blender.

Reuses the exact same wall-segment computation as the 2D renderer
(_wall_geometry: room edges unioned, then door/window intervals subtracted)
so the 3D shell can never disagree with the 2D floor plan â€” same principle
as the rest of this pipeline: one source of truth, multiple renderers.

Kept deliberately separate from the SVG pipeline for now â€” this is Step 3,
to be wired in only after it's verified standalone.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.renderer import _wall_geometry
from agent.openings import opening_span
from agent.schemas import Layout

WALL_HEIGHT_FT = 9.0
WALL_THICKNESS_FT = 0.5
ROOF_THICKNESS_FT = 0.5
FLOOR_THICKNESS_FT = 0.3
FLOOR_TO_FLOOR_FT = WALL_HEIGHT_FT + FLOOR_THICKNESS_FT
WINDOW_SILL_FT = 3.0
OPENING_HEAD_FT = 7.0


@dataclass
class WallBox:
    axis: str  # "h": fixed is y, span is x. "v": fixed is x, span is y.
    fixed: float
    lo: float
    hi: float
    floor: int
    bottom: float = 0.0
    top: float = WALL_HEIGHT_FT


@dataclass
class RoomSlab:
    x: float
    y: float
    width: float
    length: float
    floor: int
    name: str


@dataclass
class Scene:
    plot_width_ft: float
    plot_length_ft: float
    wall_height_ft: float
    wall_thickness_ft: float
    floor_to_floor_ft: float
    walls: list[WallBox] = field(default_factory=list)
    windows: list[WallBox] = field(default_factory=list)
    floor_slabs: list[RoomSlab] = field(default_factory=list)
    num_floors: int = 1

    def to_dict(self) -> dict:
        return {
            "plot_width_ft": self.plot_width_ft,
            "plot_length_ft": self.plot_length_ft,
            "wall_height_ft": self.wall_height_ft,
            "wall_thickness_ft": self.wall_thickness_ft,
            "floor_to_floor_ft": self.floor_to_floor_ft,
            "num_floors": self.num_floors,
            "walls": [vars(w) for w in self.walls],
            "windows": [vars(w) for w in self.windows],
            "floor_slabs": [vars(s) for s in self.floor_slabs],
        }


def build_scene(layout: Layout) -> Scene:
    floors = sorted({room.floor for room in layout.rooms})
    scene = Scene(
        plot_width_ft=layout.plot_width_ft,
        plot_length_ft=layout.plot_length_ft,
        wall_height_ft=WALL_HEIGHT_FT,
        wall_thickness_ft=WALL_THICKNESS_FT,
        floor_to_floor_ft=FLOOR_TO_FLOOR_FT,
        num_floors=len(floors) or 1,
    )
    for floor in floors:
        rooms = [r for r in layout.rooms if r.floor == floor]
        for axis, fixed, lo, hi in _wall_geometry(rooms):
            scene.walls.append(WallBox(axis=axis, fixed=fixed, lo=lo, hi=hi, floor=floor))
        # The plan's cuts describe horizontal spans, not full-height voids.
        # Restore masonry above doors and above/below windows; deduplicate the
        # two room records for every physical interior doorway.
        seen = set()
        for room in rooms:
            for opening in [*room.doors, *room.windows]:
                axis, fixed, lo, hi = opening_span(room, opening)
                is_window = opening in room.windows
                key = (axis, round(fixed, 3), round(lo, 3), round(hi, 3), is_window)
                if key in seen:
                    continue
                seen.add(key)
                scene.walls.append(WallBox(axis, fixed, lo, hi, floor, OPENING_HEAD_FT, WALL_HEIGHT_FT))
                if is_window:
                    scene.walls.append(WallBox(axis, fixed, lo, hi, floor, 0.0, WINDOW_SILL_FT))
                    scene.windows.append(WallBox(axis, fixed, lo, hi, floor, WINDOW_SILL_FT, OPENING_HEAD_FT))
        for room in rooms:
            scene.floor_slabs.append(RoomSlab(
                x=room.x, y=room.y, width=room.width, length=room.length,
                floor=floor, name=room.name,
            ))
    return scene

