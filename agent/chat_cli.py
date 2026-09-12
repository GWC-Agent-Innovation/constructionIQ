"""Terminal chat for step 0: talk to the bot, watch data/sessions/<id>.json fill in.

Run with: ./.venv/Scripts/python.exe agent/chat_cli.py [session_id]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from agent import spec_store  # noqa: E402
from agent.spec_agent import run_turn  # noqa: E402


def main() -> None:
    session_id = sys.argv[1] if len(sys.argv) > 1 else "default"
    session = spec_store.load(session_id)
    print(f"[session: {session_id}] type 'quit' to exit, 'spec' to print the current JSON\n")

    while True:
        user_message = input("You: ").strip()
        if not user_message:
            continue
        if user_message.lower() == "quit":
            break
        if user_message.lower() == "spec":
            print(session.spec.model_dump_json(indent=2))
            continue

        reply = run_turn(session, user_message)
        spec_store.save(session)
        print(f"Bot: {reply}\n")

    print(f"\nSaved to {spec_store._path(session_id)}")


if __name__ == "__main__":
    main()

