"""Invokes Blender headless to turn scene JSON into a .glb/.png/.blend via
agent/blender_script.py's bpy code."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

BLENDER_EXE = os.environ.get(
    "BLENDER_EXE",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
)


def run_blender(scene_path: Path, out_dir: Path, session_id: str) -> None:
    """Raises RuntimeError with Blender's stderr on failure."""
    if not shutil.which(BLENDER_EXE) and not Path(BLENDER_EXE).exists():
        raise RuntimeError(f"Blender executable not found at: {BLENDER_EXE} (set BLENDER_EXE env var).")
    script = Path(__file__).resolve().parent / "blender_script.py"
    result = subprocess.run(
        [BLENDER_EXE, "--background", "--python", str(script), "--", str(scene_path), str(out_dir), session_id],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Blender exited with an error:\n{result.stderr[-3000:]}")
