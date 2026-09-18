"""FastAPI service for the ConstructIQ planning agent.

The API wraps the existing agent pipeline. It does not duplicate design logic:
messages still go to spec_agent, generation still goes to layout_agent and
validator, and accepted plans still use the 2D renderer and Three.js viewer.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Path as ApiPath, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

from agent import spec_store
from agent.logging_setup import configure_server_logging
from agent.pipeline import generate_and_save
from agent.spec_agent import run_turn
from agent.schemas import SessionState, Wall, ChatTurn
from agent.project_undo import is_undo_request, undo_last_edit
from agent.room_edit import resize_room, swap_rooms
from agent.viewer_html import build_viewer_html

# Mirror console output into data/logs/backend.log before anything else prints,
# so every [Chat]/[Generate]/[layout] line survives an uvicorn --reload restart.
LOG_FILE = configure_server_logging()

# The API is often started from another working directory, so load the project
# .env by its absolute project path instead of relying on the caller's shell.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(
    title="ConstructIQ API",
    version="1.0.0",
    description="API for requirements capture, LLM-owned residential planning and 3D output.",
)
logger = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Generation can take minutes. These locks stop two requests for the same house
# from reading and overwriting each other's session state at the same time.
_session_locks: dict[str, asyncio.Lock] = {}


class SessionCreateRequest(BaseModel):
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    project_name: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("project_name", mode="before")
    @classmethod
    def clean_project_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    auto_generate: bool = Field(default=False, description="True from a caller (e.g. the 3D "
                                "viewer's AI Designer panel) that regenerates automatically right "
                                "after this reply, so the model shouldn't tell the user to click "
                                "a Generate button.")


class GenerationRequest(BaseModel):
    edit_request: str | None = Field(default=None, max_length=8_000)
    build_3d: bool = True


def _lock_for(session_id: str) -> asyncio.Lock:
    return _session_locks.setdefault(session_id, asyncio.Lock())


def _user_facing_issues(messages: list[str]) -> list[str]:
    """generate_layout's messages are written for the retry loop and the server
    log, not a homeowner. Format failures are an internal bug/model-compliance
    detail, not something the user can act on, so replace them with a single
    plain-language line; keep everything else (raw service errors, geometry
    violations) since those are already reasonably readable and specific."""
    if messages and all(message.startswith("Invalid layout data:") for message in messages):
        return ["The design service had trouble producing a valid response this time. Please try generating again."]
    return messages


def _session_path_or_400(session_id: str) -> Path:
    try:
        return spec_store._path(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _load_session_or_404(session_id: str) -> SessionState:
    path = _session_path_or_400(session_id)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return spec_store.load(session_id)


def _asset_paths(session_id: str) -> dict[str, Path]:
    folder = _session_path_or_400(session_id).parent
    candidates = (
        f"{session_id}.svg", f"{session_id}.png", f"{session_id}_3d.html",
    )
    return {name: folder / name for name in candidates if (folder / name).is_file()}


def _session_response(session: SessionState) -> dict:
    assets = _asset_paths(session.session_id)
    if session.model_3d_version is not None and (
            session.layout is None or session.model_3d_version != session.layout.version):
        assets = {name: path for name, path in assets.items()
                  if not name.endswith((".glb", "_3d.html", "_3d.png"))}
    asset_urls = {
        name: f"/sessions/{session.session_id}/assets/{name}"
        for name in assets
    }
    return {
        "session": session.model_dump(mode="json"),
        "ready_for_generation": session.spec.is_ready_for_layout() and (session.spec.num_floors or 1) == 1,
        "assets": asset_urls,
        "viewer_url": asset_urls.get(f"{session.session_id}_3d.html"),
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sessions")
async def list_sessions() -> dict:
    sessions = await run_in_threadpool(spec_store.list_all)
    return {"sessions": sessions}


@app.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreateRequest) -> dict:
    if not body.project_name and not body.session_id:
        raise HTTPException(status_code=422, detail="Enter a project name to begin.")
    slug = re.sub(r"[^a-z0-9]+", "-", (body.project_name or "house").lower()).strip("-")[:55] or "house"
    session_id = body.session_id or f"{slug}-{uuid4().hex[:10]}"
    path = _session_path_or_400(session_id)
    if path.exists():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session ID already exists.")
    session = SessionState(session_id=session_id, project_name=body.project_name or body.session_id)
    await run_in_threadpool(spec_store.save, session)
    print(f"\n[Project] Created new design session: '{session.project_name}' (ID: {session_id})", flush=True)
    return _session_response(session)


@app.get("/sessions/{session_id}")
async def get_session(session_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")) -> dict:
    return _session_response(_load_session_or_404(session_id))


@app.post("/sessions/{session_id}/messages")
async def add_message(body: MessageRequest, session_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")) -> dict:
    async with _lock_for(session_id):
        session = _load_session_or_404(session_id)
        original = session.model_copy(deep=True)
        if session.layout is not None:
            session.revision_specs.setdefault(session.layout.version, session.spec.model_copy(deep=True))
        if is_undo_request(body.message):
            session.history.append(ChatTurn(role='user', content=body.message))
            output, issues = await run_in_threadpool(undo_last_edit, session)
            applied = output is not None
            reply = 'Restored the previous saved design.' if applied else ' '.join(issues)
            session.history.append(ChatTurn(role='assistant', content=reply))
            await run_in_threadpool(spec_store.save, session)
            response = _session_response(session)
            response.update(reply=reply, edit_applied=applied, issues=issues)
            return response
        edit_applied = False
        edit_issues = []
        print(f"\n[Chat] [{session_id}] User: {body.message}", flush=True)
        try:
            tool_called: list[bool] = []
            reply = await run_in_threadpool(run_turn, session, body.message, body.auto_generate, tool_called)
            if body.auto_generate:
                # Not just a spec diff: the model can legitimately re-record
                # an unchanged field (e.g. re-submitting identical style_notes
                # after being asked to apply a request it had already noted)
                # - that's still a genuine attempt worth rebuilding for, not
                # a silent no-op the user has to notice and re-ask about.
                if session.spec != original.spec or (tool_called and tool_called[0]):
                    output, edit_issues = await run_in_threadpool(
                        generate_and_save, session, body.message, True, False, True,
                    )
                    edit_applied = output is not None
                    if not edit_applied:
                        session.spec = original.spec
                        session.layout = original.layout
                        session.layout_history = original.layout_history
                        session.model_3d_version = original.model_3d_version
                        session.layout_options = original.layout_options
                        reply = "Your previous design has been kept. " + " ".join(edit_issues)
                    else:
                        reply = "Your changes have been applied to the floor plan and 3D model."
                else:
                    reply = "No changes applied. " + reply
                session.history[-1].content = reply
            await run_in_threadpool(spec_store.save, session)
            print(f"[Chat] [{session_id}] AI Architect: {reply[:120]}...\n", flush=True)
        except Exception as exc:
            print(f"[Error] [{session_id}] Turn failed: {exc}\n", flush=True)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                                detail=f"Requirement agent failed: {exc}") from exc
    response = _session_response(session)
    response["reply"] = reply
    response["edit_applied"] = edit_applied
    response["issues"] = edit_issues
    return response


class LayoutChoiceRequest(BaseModel):
    layout_version: int = Field(ge=1)
    option_index: int = Field(ge=0, le=2)


@app.post("/sessions/{session_id}/layout-choice")
async def choose_layout(body: LayoutChoiceRequest, session_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")) -> dict:
    async with _lock_for(session_id):
        session = _load_session_or_404(session_id)
        if session.layout is None or session.layout.version != body.layout_version:
            raise HTTPException(status_code=409, detail="The plan has changed. Reload to see current design options.")
        if body.option_index >= len(session.layout_options):
            raise HTTPException(status_code=404, detail="Design option not found.")
        output, issues = await run_in_threadpool(
            generate_and_save, session, None, True, False, True,
            session.layout_options[body.option_index],
        )
        if output is None:
            raise HTTPException(status_code=422, detail={"issues": issues})
        return _session_response(session)


class WallPaintRequest(BaseModel):
    layout_version: int = Field(ge=1)
    room_id: str = Field(min_length=1, max_length=100)
    wall: Wall
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


@app.post("/sessions/{session_id}/wall-paint")
async def paint_wall(body: WallPaintRequest, session_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")) -> dict:
    async with _lock_for(session_id):
        session = _load_session_or_404(session_id)
        if session.layout is None or session.layout.version != body.layout_version:
            raise HTTPException(status_code=409, detail="The floor plan has changed. Reload before painting.")
        if not any(room.id == body.room_id for room in session.layout.rooms):
            raise HTTPException(status_code=404, detail="Room not found in the current plan.")
        key = f"{body.room_id}:{body.wall}"
        if body.color is None:
            session.layout.wall_colors.pop(key, None)
        else:
            session.layout.wall_colors[key] = body.color.lower()
        for previous in session.layout_history:
            if previous.version == session.layout.version:
                previous.wall_colors = dict(session.layout.wall_colors)
        await run_in_threadpool(spec_store.save, session)
        return {"wall_key": key, "color": session.layout.wall_colors.get(key),
                "layout_version": session.layout.version}


class RoomEditRequest(BaseModel):
    layout_version: int = Field(ge=1)
    action: Literal["resize", "swap"]
    room_id: str = Field(min_length=1, max_length=100)
    other_room_id: str | None = Field(default=None, min_length=1, max_length=100)
    edge: Wall | None = None
    delta_ft: float | None = None


@app.post("/sessions/{session_id}/room-edit")
async def edit_room(body: RoomEditRequest, session_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")) -> dict:
    async with _lock_for(session_id):
        session = _load_session_or_404(session_id)
        if session.layout is None or session.layout.version != body.layout_version:
            raise HTTPException(status_code=409, detail="The floor plan has changed. Reload before editing.")
        room_ids = {room.id for room in session.layout.rooms}
        if body.room_id not in room_ids:
            raise HTTPException(status_code=404, detail="Room not found in the current plan.")
        if body.action == "resize":
            if body.edge is None or body.delta_ft is None:
                raise HTTPException(status_code=422, detail="edge and delta_ft are required to resize a room.")
            new_layout, issues = resize_room(session.layout, session.spec, body.room_id, body.edge, body.delta_ft)
        else:
            if body.other_room_id is None:
                raise HTTPException(status_code=422, detail="other_room_id is required to swap rooms.")
            if body.other_room_id not in room_ids:
                raise HTTPException(status_code=404, detail="Room not found in the current plan.")
            new_layout, issues = swap_rooms(session.layout, session.spec, body.room_id, body.other_room_id)
        if new_layout is None:
            raise HTTPException(status_code=422, detail={"issues": issues})
        output, issues = await run_in_threadpool(
            generate_and_save, session, None, True, False, True, new_layout,
        )
        if output is None:
            raise HTTPException(status_code=422, detail={"issues": issues})
        return _session_response(session)


@app.post("/sessions/{session_id}/generate")
async def generate(body: GenerationRequest, session_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")) -> dict:
    """Synchronously create a validated plan and optional 3D model for one session."""
    async with _lock_for(session_id):
        session = _load_session_or_404(session_id)
        print(f"\n[Generate] [{session_id}] Starting layout computation & Three.js scene preparation...", flush=True)
        try:
            output, messages = await run_in_threadpool(
                generate_and_save, session, body.edit_request, body.build_3d, False,
            )
        except Exception as exc:
            logger.exception("Unexpected generation failure for session %s", session_id)
            print(f"[Error] [{session_id}] Generation failed: {exc}\n", flush=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Generation failed unexpectedly. Check the API server log for details.") from exc
    if output is None:
        error_status = (status.HTTP_503_SERVICE_UNAVAILABLE if any(
            message.startswith("LLM planning service failed:") for message in messages
        ) else 422)
        print(f"[Generate] [{session_id}] Layout validation incomplete: {messages}\n", flush=True)
        raise HTTPException(status_code=error_status,
                            detail={"issues": _user_facing_issues(messages)})
    print(f"[Generate] [{session_id}] Layout & 3D model successfully generated: {output.name}\n", flush=True)
    response = _session_response(session)
    response["warnings"] = messages
    if body.build_3d and any(message.startswith("3D model build failed:") for message in messages):
        # An earlier viewer may still exist on disk. It is not this revision.
        response["viewer_url"] = None
        response["assets"] = {name: url for name, url in response["assets"].items()
                             if not name.endswith((".glb", "_3d.html", "_3d.png"))}
    response["floor_plan_url"] = f"/sessions/{session_id}/assets/{output.name}"
    return response


@app.get("/sessions/{session_id}/assets/{asset_name}")
async def get_asset(
    session_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$"),
    asset_name: str = ApiPath(pattern=r"^[A-Za-z0-9_.-]{1,100}$"),
) -> FileResponse:
    session = _load_session_or_404(session_id)
    asset = _asset_paths(session_id).get(asset_name)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    if asset_name == f"{session_id}_3d.html":
        if session.layout is None or session.model_3d_version not in (None, session.layout.version):
            raise HTTPException(status_code=404, detail="Generate a 3D model for the current plan first.")
        # Existing saved homes also use the current Three.js renderer on open.
        html = await run_in_threadpool(build_viewer_html, session.layout, session.spec,
                                       session.project_name)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})
    media_types = {
        ".svg": "image/svg+xml", ".png": "image/png", ".html": "text/html", ".glb": "model/gltf-binary",
    }
    return FileResponse(asset, media_type=media_types.get(asset.suffix, "application/octet-stream"),
                        headers={"Cache-Control": "no-store"})


# Mount the browser interface after API routes so it can use the same origin
# without CORS configuration. Visit http://127.0.0.1:8000 after starting serve.py.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
