"""Generate, validate and render a floor plan from a saved chat session."""
from __future__ import annotations
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from agent import spec_store
from agent.pipeline import generate_and_save

def main():
    if len(sys.argv) < 2:
        print("Usage: python agent/run_pipeline.py <session_id>")
        return 1
    session = spec_store.load(sys.argv[1])
    path, issues = generate_and_save(session, open_browser=True)
    if path is None:
        print("Could not generate a valid plan:\n" + "\n".join(issues))
        return 1
    print(f"Saved floor plan: {path}")
    for warning in issues:
        print(f"Warning: {warning}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

