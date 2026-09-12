"""Standalone entry point for the 3D step: build a Blender model (and the
interactive HTML viewer) from an existing session's layout. This is what
generate_and_save() now runs automatically after every 'generate'; this
script is for rebuilding those assets on their own, without regenerating the
2D layout (e.g. after tweaking viewer_html.py or blender_script.py).

Run with: ./.venv/Scripts/python.exe agent/build_3d_model.py <session_id>
Requires the session to already have a generated layout (run_pipeline.py or
chat.py's 'generate' first). Produces <session_id>_3d.png, <session_id>.glb/.blend and
<session_id>_3d.html under data/sessions/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import spec_store  # noqa: E402
from agent.pipeline import build_3d_assets  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: build_3d_model.py <session_id>")
        sys.exit(1)

    session_id = sys.argv[1]
    session = spec_store.load(session_id)
    if session.layout is None:
        print("No layout yet for this session â€” run run_pipeline.py or chat.py first.")
        sys.exit(1)

    try:
        viewer_path = build_3d_assets(session_id, session.layout, session.spec, session.project_name)
    except Exception as exc:
        print(f"3D build failed: {exc}")
        sys.exit(1)

    out_dir = viewer_path.parent
    print(f"Done. See {out_dir / session_id}_3d.png, {out_dir / session_id}.glb / .blend and {viewer_path}")


if __name__ == "__main__":
    main()

