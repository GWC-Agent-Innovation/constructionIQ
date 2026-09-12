"""Persists each session's chat history + gathered HouseSpec as a JSON file."""
from __future__ import annotations

from pathlib import Path
import re
import os
import tempfile

from agent.schemas import SessionState

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"


def _path(session_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
        raise ValueError("Session ID must contain 1-80 letters, numbers, underscores or hyphens.")
    return DATA_DIR / f"{session_id}.json"


def load(session_id: str) -> SessionState:
    path = _path(session_id)
    if path.exists():
        return SessionState.model_validate_json(path.read_text(encoding="utf-8"))
    return SessionState(session_id=session_id)


def save(session: SessionState) -> None:
    target = _path(session.session_id)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=DATA_DIR, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(session.model_dump_json(indent=2))
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def list_all() -> list[dict]:
    if not DATA_DIR.exists():
        return []
    results = []
    for file_path in sorted(DATA_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        session_id = file_path.stem
        try:
            session = SessionState.model_validate_json(file_path.read_text(encoding="utf-8"))
            spec = session.spec
            has_3d = (DATA_DIR / f"{session_id}_3d.html").exists() or (DATA_DIR / f"{session_id}.glb").exists()
            results.append({
                "session_id": session.session_id,
                "project_name": session.project_name or session.session_id,
                "updated_at": file_path.stat().st_mtime,
                "has_layout": session.layout is not None,
                "has_3d": has_3d,
                "plot_info": f"{spec.plot_width_ft:g} x {spec.plot_length_ft:g} ft" if spec.plot_width_ft and spec.plot_length_ft else None,
                "rooms_info": f"{spec.num_bedrooms} beds, {spec.num_bathrooms} baths" if spec.num_bedrooms is not None and spec.num_bathrooms is not None else None,
                "turns_count": len(session.history),
            })
        except Exception:
            continue
    return results

