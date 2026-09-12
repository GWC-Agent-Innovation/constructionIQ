"""Step 2: render a session's layout to an SVG file you can open in a browser/VS Code.

Run with: ./.venv/Scripts/python.exe agent/render_layout.py <session_id>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import spec_store  # noqa: E402
from agent.renderer import render_svg, render_png  # noqa: E402
from agent.validator import validate_layout  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: render_layout.py <session_id>")
        sys.exit(1)

    session_id = sys.argv[1]
    session = spec_store.load(session_id)

    if session.layout is None:
        print("No layout yet for this session â€” run plan_layout.py first.")
        sys.exit(1)

    issues = validate_layout(session.layout, session.spec)
    if issues:
        print("Saved plan needs regeneration with the updated validator:")
        for issue in issues:
            print(" -", issue.message)
        sys.exit(1)
    svg = render_svg(session.layout)
    out_path = Path(__file__).resolve().parent.parent / "data" / "sessions" / f"{session_id}.svg"
    out_path.write_text(svg, encoding="utf-8")
    out_path.with_suffix(".png").write_bytes(render_png(session.layout))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

