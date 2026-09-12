"""Builds a 3D house model from scene JSON (see agent/model3d.py) inside Blender.

This file only runs inside Blender's own Python (bpy) — never imported by
the rest of the app. Invoke via:
  blender --background --python agent/blender_script.py -- <scene.json> <out_dir> [<prefix>]

Kept deliberately separate from the SVG pipeline — Step 3, standalone until
verified, then wired into generate_and_save().
"""
import json
import math
import sys
from pathlib import Path

import bpy

WALL_COLOR = (0.86, 0.84, 0.80, 1.0)
FLOOR_COLOR = (0.55, 0.50, 0.45, 1.0)
ROOF_COLOR = (0.35, 0.30, 0.28, 1.0)


def _material(name: str, rgba) -> "bpy.types.Material":
    existing = bpy.data.materials.get(name)
    if existing:
        return existing
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = rgba
        principled.inputs["Roughness"].default_value = 0.8
    return mat


def _add_box(name: str, x, y, z, sx, sy, sz, material):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, z))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (sx, sy, sz)
    obj.data.materials.append(material)
    return obj


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block_type in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for block in list(block_type):
            if block.users == 0:
                block_type.remove(block)


def build(scene: dict):
    clear_scene()
    wall_mat = _material("Wall", WALL_COLOR)
    floor_mat = _material("Floor", FLOOR_COLOR)
    roof_mat = _material("Roof", ROOF_COLOR)
    glass_mat = _material("Window glazing", (0.25, 0.49, 0.63, 1.0))
    frame_mat = _material("Window frame", (0.10, 0.14, 0.17, 1.0))
    glass_shader = glass_mat.node_tree.nodes.get("Principled BSDF")
    glass_shader.inputs["Roughness"].default_value = 0.18
    glass_shader.inputs["Metallic"].default_value = 0.25

    thickness = scene["wall_thickness_ft"]
    wall_height = scene["wall_height_ft"]
    floor_to_floor = scene["floor_to_floor_ft"]
    plot_w, plot_l = scene["plot_width_ft"], scene["plot_length_ft"]
    floor_thickness = 0.3
    roof_thickness = 0.5

    def bx(layout_x):
        return layout_x

    def by(layout_y):
        return -layout_y  # layout y increases south; flip so it doesn't mirror the plan visually

    floors_present = sorted({w["floor"] for w in scene["walls"]} | {s["floor"] for s in scene["floor_slabs"]}) or [0]

    for floor in floors_present:
        base_z = floor * floor_to_floor

        _add_box(
            f"floor_slab_{floor}",
            bx(plot_w / 2), by(plot_l / 2), base_z - floor_thickness / 2,
            plot_w, plot_l, floor_thickness, floor_mat,
        )

        for i, wall in enumerate(w for w in scene["walls"] if w["floor"] == floor):
            length = wall["hi"] - wall["lo"]
            if length <= 0:
                continue
            mid = (wall["lo"] + wall["hi"]) / 2
            bottom, top = wall.get('bottom', 0), wall.get('top', wall_height)
            height = top - bottom
            center_z = base_z + (bottom + top) / 2
            if wall["axis"] == "h":
                _add_box(f"wall_{floor}_{i}", bx(mid), by(wall["fixed"]), center_z,
                         length, thickness, height, wall_mat)
            else:
                _add_box(f"wall_{floor}_{i}", bx(wall["fixed"]), by(mid), center_z,
                         thickness, length, height, wall_mat)

        for i, window in enumerate(w for w in scene.get('windows', []) if w['floor'] == floor):
            lo, hi = window['lo'], window['hi']
            bottom, top = window['bottom'], window['top']

            def window_piece(label, start, end, zlo, zhi, depth, material):
                mid = (start + end) / 2
                z = base_z + (zlo + zhi) / 2
                if window['axis'] == 'h':
                    _add_box(f'window_{floor}_{i}_{label}', bx(mid), by(window['fixed']), z,
                             end-start, depth, zhi-zlo, material)
                else:
                    _add_box(f'window_{floor}_{i}_{label}', bx(window['fixed']), by(mid), z,
                             depth, end-start, zhi-zlo, material)

            frame = 0.12
            window_piece('pane', lo+frame, hi-frame, bottom+frame, top-frame, 0.06, glass_mat)
            window_piece('sill', lo, hi, bottom, bottom+frame, thickness, frame_mat)
            window_piece('head', lo, hi, top-frame, top, thickness, frame_mat)
            window_piece('left', lo, lo+frame, bottom+frame, top-frame, thickness, frame_mat)
            window_piece('right', hi-frame, hi, bottom+frame, top-frame, thickness, frame_mat)
            window_piece('mullion', (lo+hi-frame)/2, (lo+hi+frame)/2,
                         bottom+frame, top-frame, 0.15, frame_mat)

        _add_box(
            f"roof_{floor}", bx(plot_w / 2), by(plot_l / 2), base_z + wall_height + roof_thickness / 2,
            plot_w + 1, plot_l + 1, roof_thickness, roof_mat,
        )

    top_floor = max(floors_present)
    model_height = top_floor * floor_to_floor + wall_height + roof_thickness
    return plot_w, plot_l, model_height


def setup_camera_and_light(plot_w, plot_l, model_height):
    span = max(plot_w, plot_l, model_height)
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = math.hypot(plot_w, plot_l) * 1.5
    cam = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (plot_w / 2 + span, -plot_l / 2 - span, span * 1.5)
    direction = (bpy.data.objects["Camera"].location -
                (bpy.context.scene.cursor.location))
    target = bpy.data.objects.new("Target", None)
    target.location = (plot_w / 2, -plot_l / 2, model_height / 3)
    bpy.context.collection.objects.link(target)
    constraint = cam.constraints.new(type="TRACK_TO")
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"
    bpy.context.scene.camera = cam

    sun_data = bpy.data.lights.new("Sun", type="SUN")
    sun_data.energy = 2.0
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.location = (plot_w / 2, -plot_l / 2, span * 2)
    sun.rotation_euler = (0.7, 0.2, 0.8)
    bpy.context.collection.objects.link(sun)

    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.22, 0.25, 0.28, 1.0)
        bg.inputs[1].default_value = 0.5


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    scene_path, out_dir = Path(args[0]), Path(args[1])
    prefix = args[2] if len(args) > 2 else "model"
    out_dir.mkdir(parents=True, exist_ok=True)

    scene_data = json.loads(scene_path.read_text(encoding="utf-8"))
    plot_w, plot_l, model_height = build(scene_data)
    setup_camera_and_light(plot_w, plot_l, model_height)

    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    bpy.context.scene.render.resolution_x = 1200
    bpy.context.scene.render.resolution_y = 900
    bpy.context.scene.render.filepath = str(out_dir / f"{prefix}_3d.png")
    roofs = [obj for obj in bpy.data.objects if obj.name.startswith('roof_')]
    for roof in roofs:
        roof.hide_render = True
    bpy.ops.render.render(write_still=True)
    for roof in roofs:
        roof.hide_render = False

    bpy.ops.export_scene.gltf(filepath=str(out_dir / f"{prefix}.glb"), export_format="GLB")
    bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / f"{prefix}.blend"))
    print(f"WROTE: {out_dir / prefix}_3d.png and {out_dir / prefix}.glb|.blend")


if __name__ == "__main__":
    main()
