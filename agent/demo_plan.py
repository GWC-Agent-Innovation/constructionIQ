"""Offline reference-style example. Run with python agent/demo_plan.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.door_planner import plan_openings
from agent.renderer import render_svg, render_png
from agent.schemas import HouseSpec, Layout, Room
from agent.validator import validate_layout


def example():
    spec = HouseSpec(plot_width_ft=40, plot_length_ft=30, num_bedrooms=3,
                     num_bathrooms=3, num_floors=1, entrance_facing="south",
                     must_have_rooms=["Home Office", "Pooja Room"])
    zones = [
        ("office", "Home Office", 0, 0, 8, 10),
        ("pooja", "Pooja Room", 8, 0, 6, 10),
        ("kitchen", "Kitchen", 14, 0, 10, 10),
        ("master", "Master Bedroom", 24, 0, 10, 10),
        ("bath1", "Master Bathroom", 34, 0, 6, 10),
        ("living", "Living Room", 0, 10, 14, 8),
        ("dining", "Dining Area", 14, 10, 10, 8),
        ("hall", "Hallway", 24, 10, 16, 8),
        ("foyer", "Entrance Foyer", 0, 18, 8, 12),
        ("bed2", "Bedroom 2", 8, 18, 12, 12),
        ("bed3", "Bedroom 3", 20, 18, 12, 12),
        ("bath2", "Family Bathroom", 32, 18, 8, 6),
        ("bath3", "Bedroom 3 Bathroom", 32, 24, 8, 6),
    ]
    layout = Layout(plot_width_ft=40, plot_length_ft=30,
                    rooms=[Room(id=id, name=name, x=x, y=y, width=w, length=h)
                           for id, name, x, y, w, h in zones])
    return spec, plan_openings(layout, spec)


if __name__ == "__main__":
    spec, layout = example()
    prefix = "reference_plan"
    if "--refresh-live" in sys.argv:
        prefix = "generated_plan"
        saved = Path(__file__).resolve().parent.parent / "data" / "examples" / "generated_plan.json"
        layout = plan_openings(Layout.model_validate_json(saved.read_text(encoding="utf-8")), spec)
    if "--live" in sys.argv:
        from dotenv import load_dotenv
        load_dotenv()
        from agent.layout_agent import generate_layout
        layout, failures = generate_layout(spec)
        if layout is None:
            raise SystemExit("\n".join(failures))
        prefix = "generated_plan"
    issues = validate_layout(layout, spec)
    if issues:
        raise SystemExit("\n".join(issue.message for issue in issues))
    output = Path(__file__).resolve().parent.parent / "data" / "examples"
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{prefix}.svg").write_text(render_svg(layout), encoding="utf-8")
    (output / f"{prefix}.png").write_bytes(render_png(layout))
    (output / f"{prefix}.json").write_text(layout.model_dump_json(indent=2), encoding="utf-8")
    print(f"Validated example: {output / prefix}")

