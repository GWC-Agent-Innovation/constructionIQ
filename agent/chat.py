"""The connected pipeline: chat for requirements, then generate the floor plan
in the same session.

Run with: ./.venv/Scripts/python.exe agent/chat.py <session_id>
Chat naturally to describe your house. Once plot size and bedroom/bathroom
counts are known, a first plan is generated automatically. Type 'generate'
to retry or request edits naturally. Outputs include SVG, PNG and a browser 3D viewer.
"""
from __future__ import annotations

import sys
import re
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from agent import spec_store  # noqa: E402
from agent.spec_agent import run_turn  # noqa: E402
from agent.pipeline import generate_and_save  # noqa: E402

GENERATE_COMMANDS = {"generate", "svg", "floor plan", "floorplan", "blueprint"}


def _is_generate_request(message: str) -> bool:
    lowered = message.lower()
    if re.search(r"\b(don't|do not|not yet|wait)\b", lowered):
        return False
    return lowered in GENERATE_COMMANDS or bool(re.search(r"\b(generate|draw|create|render)\b", lowered))


def run_generation(session, edit_request=None, open_browser=True) -> bool:
    print("\nGenerating your floor plan (this can take a few minutes)...")
    if not session.spec.is_ready_for_layout():
        print("I still need plot dimensions and bedroom/bathroom counts.\n")
        return False

    try:
        out_path, issues = generate_and_save(session, edit_request, open_browser=open_browser)
    except Exception as exc:
        print(f"Generation failed: {exc}. Your requirements are saved; type generate to retry.")
        return False
    if out_path is None:
        print("Could not produce a valid layout:")
        for issue in issues:
            print(" -", issue)
        print()
        return False

    layout = session.layout
    covered = sum(r.width * r.length for r in layout.rooms)
    total = layout.plot_width_ft * layout.plot_length_ft
    print(f"Done! {len(layout.rooms)} rooms, {covered:g} of {total:g} sqft covered.")
    if layout.planning_notes:
        print("Planning notes:")
        for note in layout.planning_notes:
            print(f" - {note}")
    print(f"2D floor plan (optional): {out_path}")
    for warning in issues:
        print(f"Warning: {warning}\n")
    viewer_path = out_path.with_name(out_path.stem + "_3d.html")
    if not issues and viewer_path.exists():
        print(f"OPEN THIS for interactive 3D: {viewer_path}")
        print(f"Rendered 3D image: {out_path.with_name(out_path.stem + '_3d.png')}\n")
    return not issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Describe a house, generate a plan, and open its 3D model.")
    parser.add_argument("session_id", nargs="?", default="default")
    parser.add_argument("--requirements", help="Generate once from this description, then exit.")
    parser.add_argument("--no-browser", action="store_true", help="Save output without launching a browser.")
    args = parser.parse_args()
    session_id = args.session_id
    session = spec_store.load(session_id)
    already_prompted_ready = session.layout is not None

    print(f"[session: {session_id}] describe your dream house.")
    if args.no_browser:
        print("Browser launch disabled; the 3D viewer will be saved to disk.")
    else:
        print("Your 3D model opens in the browser after generation.")
    print("Type 'generate' to retry, 'quit' to exit.\n")

    if args.requirements:
        try:
            reply = run_turn(session, args.requirements)
            spec_store.save(session)
            print(f"Bot: {reply}")
        except Exception as exc:
            print(f"Could not record requirements: {exc}")
            return 1
        return 0 if run_generation(session, args.requirements, open_browser=not args.no_browser) else 1

    while True:
        try:
            user_message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_message:
            continue
        if user_message.lower() == "quit":
            break
        if user_message.lower() in GENERATE_COMMANDS:
            run_generation(session, "\n".join(t.content for t in session.history[-8:] if t.role == "user"), open_browser=not args.no_browser)
            continue

        try:
            reply = run_turn(session, user_message)
        except Exception as exc:
            print(f"Could not process requirements: {exc}. Please retry.")
            continue
        spec_store.save(session)
        print(f"Bot: {reply}")
        deferred = bool(re.search(r"\b(don't|do not|not yet|wait)\b", user_message.lower()))
        edit = session.layout is not None and bool(re.search(r"\b(move|change|resize|add|remove|replace)\b", user_message.lower()))
        if session.spec.is_ready_for_layout() and not deferred and (
                _is_generate_request(user_message) or edit or not already_prompted_ready):
            already_prompted_ready = True
            run_generation(session, "\n".join(t.content for t in session.history[-8:] if t.role == "user"), open_browser=not args.no_browser)
        print()

    print(f"\nSaved to {spec_store._path(session_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

