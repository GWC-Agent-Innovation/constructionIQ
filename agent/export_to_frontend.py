"""Exports a session's validated layout JSON directly for use in the frontend FloorPlanBlueprint.

Usage:
  ./.venv/Scripts/python.exe agent/export_to_frontend.py <session_id>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import spec_store  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python agent/export_to_frontend.py <session_id>")
        sys.exit(1)

    session_id = sys.argv[1]
    session = spec_store.load(session_id)

    if session.layout is None:
        print(f"No layout found for session '{session_id}'. Run plan_layout.py first.")
        sys.exit(1)

    layout_dict = session.layout.model_dump()
    json_str = json.dumps(layout_dict, indent=2)

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    out_file = frontend_dir / f"{session_id}_layout.json"
    out_file.write_text(json_str, encoding="utf-8")

    print(f"Exported layout JSON to {out_file}")
    print("\nYou can paste this JSON into the frontend wireframe or load it in FloorPlanBlueprint:")
    print(json_str)


if __name__ == "__main__":
    main()


