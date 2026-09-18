"""One generation/save/render path shared by the command-line entry points."""
import json
import webbrowser
import tempfile
from pathlib import Path
from agent import spec_store
from agent.layout_agent import generate_layout
from agent.model3d import build_scene
from agent.renderer import render_svg, render_png
from agent.schemas import HouseSpec, Layout, SessionState
from agent.viewer_html import build_viewer_html
from agent.validator import validate_layout


def build_3d_assets(session_id: str, layout: Layout, spec: HouseSpec, project_name: str | None = None,
                    out_dir: Path | None = None) -> Path:
    """Write scene geometry and a browser-rendered Three.js viewer."""
    out_dir = out_dir or spec_store._path(session_id).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    scene_path = out_dir / f"{session_id}_scene.json"
    scene_path.write_text(json.dumps(build_scene(layout).to_dict(), indent=2), encoding="utf-8")
    print("  [3d] preparing Three.js viewer...", flush=True)
    viewer_html = build_viewer_html(layout, spec, project_name=project_name)
    viewer_path = out_dir / f"{session_id}_3d.html"
    viewer_path.write_text(viewer_html, encoding="utf-8")
    return viewer_path


def generate_and_save(
    session: SessionState, edit_request: str | None = None, build_3d: bool = True,
    open_browser: bool = False,
    require_3d_success: bool = False,
    selected_layout: Layout | None = None,
    restore_revision: int | None = None,
) -> tuple[Path | None, list[str]]:
    target = spec_store._path(session.session_id).with_suffix(".svg")
    design_context = "\n".join(turn.content for turn in session.history if turn.role == "user")[-12000:]
    options = []
    if selected_layout is not None:
        layout = selected_layout.model_copy(deep=True)
        issues = [issue.message for issue in validate_layout(layout, session.spec)]
        if issues:
            return None, issues
    else:
        layout, issues = generate_layout(session.spec, previous_layout=session.layout, edit_request=edit_request,
                                        design_context=design_context or None, options_out=options)
    if layout is None:
        return None, issues
    if session.layout and restore_revision is None:
        previous_rooms = {r.id: (r.name, r.floor) for r in session.layout.rooms}
        retained = {r.id for r in layout.rooms if previous_rooms.get(r.id) == (r.name, r.floor)}
        layout.wall_colors = {key: color for key, color in session.layout.wall_colors.items()
                              if key.rsplit(':', 1)[0] in retained}
    layout.version = max((item.version for item in session.layout_history), default=0) + 1
    svg = render_svg(layout)
    png = render_png(layout)
    staged_assets = {}
    if require_3d_success:
        # Viewer edits must finish rendering before replacing any live assets
        # or committing the new requirements/layout.
        try:
            with tempfile.TemporaryDirectory() as folder:
                staging = Path(folder)
                build_3d_assets(session.session_id, layout, session.spec, session.project_name,
                                out_dir=staging)
                staged_assets = {path.name: path.read_bytes() for path in staging.iterdir()}
        except Exception as exc:
            return None, [f"3D model build failed: {exc}"]
    next_state = session.model_copy(deep=True)
    next_state.layout = layout
    next_state.layout_history.append(layout)
    next_state.revision_specs[layout.version] = session.spec.model_copy(deep=True)
    if restore_revision is None:
        next_state.revision_parents[layout.version] = session.layout.version if session.layout else None
    else:
        earlier = [item.version for item in session.layout_history if item.version < restore_revision]
        next_state.revision_parents[layout.version] = session.revision_parents.get(
            restore_revision, max(earlier, default=None))
    if selected_layout is None:
        next_state.layout_options = [option.model_copy(deep=True) for option in options]
    next_state.model_3d_version = layout.version if require_3d_success else 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg, encoding="utf-8")
    target.with_name(f"{target.stem}.v{layout.version}.svg").write_text(svg, encoding="utf-8")
    target.with_suffix(".png").write_bytes(png)
    target.with_name(f"{target.stem}.v{layout.version}.png").write_bytes(png)
    for name, content in staged_assets.items():
        (target.parent / name).write_bytes(content)
    spec_store.save(next_state)
    session.layout = layout
    session.layout_history = next_state.layout_history
    session.layout_options = next_state.layout_options
    session.revision_specs = next_state.revision_specs
    session.revision_parents = next_state.revision_parents
    session.model_3d_version = next_state.model_3d_version

    warnings: list[str] = []
    if build_3d and not require_3d_success:
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
