"""Rebuild a Three.js viewer from an existing generated layout.

Run: ./.venv/Scripts/python.exe agent/build_3d_model.py <session_id>
Produces <session_id>_scene.json and <session_id>_3d.html.
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

    session.model_3d_version = session.layout.version
    spec_store.save(session)
    print(f"Done. Open {viewer_path} for the Three.js model.")


if __name__ == "__main__":
    main()
