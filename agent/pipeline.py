"""One generation/save/render path shared by the command-line entry points."""
import json
import webbrowser
from pathlib import Path
from agent import spec_store
from agent.blender_runner import run_blender
from agent.layout_agent import generate_layout
from agent.model3d import build_scene
from agent.renderer import render_svg, render_png
from agent.schemas import HouseSpec, Layout, SessionState
from agent.viewer_html import build_viewer_html


def build_3d_assets(session_id: str, layout: Layout, spec: HouseSpec, project_name: str | None = None) -> Path:
    """Builds the .glb/.png/.blend via headless Blender, then the standalone
    interactive HTML viewer on top of them. Returns the viewer's path.
    Raises on failure â€” the caller decides whether that should be fatal."""
    out_dir = spec_store._path(session_id).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    scene_path = out_dir / f"{session_id}_scene.json"
    scene_path.write_text(json.dumps(build_scene(layout).to_dict(), indent=2), encoding="utf-8")
    print("  [3d] building mesh in Blender (headless)...", flush=True)
    run_blender(scene_path, out_dir, session_id)
    glb_bytes = (out_dir / f"{session_id}.glb").read_bytes()
    poster_bytes = (out_dir / f"{session_id}_3d.png").read_bytes()
    print("  [3d] building interactive viewer...", flush=True)
    viewer_html = build_viewer_html(layout, spec, glb_bytes, poster_bytes, project_name=project_name)
    viewer_path = out_dir / f"{session_id}_3d.html"
    viewer_path.write_text(viewer_html, encoding="utf-8")
    return viewer_path


def generate_and_save(
    session: SessionState, edit_request: str | None = None, build_3d: bool = True,
    open_browser: bool = False,
) -> tuple[Path | None, list[str]]:
    target = spec_store._path(session.session_id).with_suffix(".svg")
    design_context = "\n".join(turn.content for turn in session.history if turn.role == "user")[-12000:]
    layout, issues = generate_layout(session.spec, previous_layout=session.layout, edit_request=edit_request,
                                    design_context=design_context or None)
    if layout is None:
        return None, issues
    layout.version = max((item.version for item in session.layout_history), default=0) + 1
    svg = render_svg(layout)
    png = render_png(layout)
    next_state = session.model_copy(deep=True)
    next_state.layout = layout
    next_state.layout_history.append(layout)
    next_state.model_3d_version = 0
    spec_store.save(next_state)
    target.write_text(svg, encoding="utf-8")
    target.with_name(f"{target.stem}.v{layout.version}.svg").write_text(svg, encoding="utf-8")
    target.with_suffix(".png").write_bytes(png)
    target.with_name(f"{target.stem}.v{layout.version}.png").write_bytes(png)
    session.layout = layout
    session.layout_history = next_state.layout_history
    session.model_3d_version = 0

    warnings: list[str] = []
    if build_3d:
        try:
            viewer_path = build_3d_assets(session.session_id, layout, session.spec, session.project_name)
            session.model_3d_version = layout.version
            spec_store.save(session)
            if open_browser:
                try:
                    opened = webbrowser.open(viewer_path.resolve().as_uri() + "#model", new=2)
                    if not opened:
                        warnings.append(f"Browser could not open automatically. Open {viewer_path} manually.")
                except Exception as exc:
                    warnings.append(f"Browser could not open automatically: {exc}. Open {viewer_path} manually.")
        except Exception as exc:  # 3D is best-effort: the 2D plan already succeeded
            warnings.append(f"3D model build failed: {exc}")

    return target, warnings
