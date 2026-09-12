"""Start ConstructIQ's local FastAPI backend.

Run: ./.venv/Scripts/python.exe backend/serve.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from agent.logging_setup import LOG_FILE


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    print("\n" + "=" * 60, flush=True)
    print("  🚀 ConstructIQ Design Studio Backend Started", flush=True)
    print("  👉 Open in your web browser: http://127.0.0.1:8000", flush=True)
    print(f"  📝 Full activity log: {LOG_FILE}", flush=True)
    print("=" * 60 + "\n", flush=True)
    uvicorn.run("backend.api:app", host="127.0.0.1", port=8000, reload=True,
                access_log=True,
                reload_dirs=[str(project_root / "agent"), str(project_root / "backend")])

