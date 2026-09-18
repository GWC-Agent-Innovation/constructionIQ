"""Data model.

Step 0: HouseSpec — the chatbot's understanding of the user's requirements.
Step 1: DesignHints — lightweight design judgment (room-size emphasis,
adjacency preferences) the LLM contributes; it never emits coordinates.
Step 2: Layout/Room/Door — the deterministic solver's room-by-room geometry,
decided once the spec is complete enough. Rendering comes later, from this
same structured data, never an image emitted by the LLM.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict

Wall = Literal["north", "south", "east", "west"]


class RequiredAdjacency(BaseModel):
    a: str = Field(min_length=1, max_length=80)
    b: str = Field(min_length=1, max_length=80)


class HouseSpec(BaseModel):
    """Requirements gathered from the conversation. Filled in incrementally."""
    model_config = ConfigDict(validate_assignment=True, allow_inf_nan=False)
    plot_width_ft: Optional[float] = Field(default=None, gt=0)
    plot_length_ft: Optional[float] = Field(default=None, gt=0)
    num_bedrooms: Optional[int] = Field(default=None, ge=1)
    num_bathrooms: Optional[int] = Field(default=None, ge=1)
    num_floors: Optional[int] = Field(default=None, ge=1)
    must_have_rooms: list[str] = Field(default_factory=list)
    style_notes: Optional[str] = None
    required_adjacencies: list[RequiredAdjacency] = Field(default_factory=list, max_length=20)
    entrance_facing: Optional[str] = None
    budget_notes: Optional[str] = None
    max_bathroom_area_sqft: float = Field(default=80.0, ge=25)
    max_parking_area_sqft: float = Field(default=250.0, ge=25)
    max_bathroom_bedroom_ratio: float = Field(default=0.75, gt=0)
    attached_bathroom_bedrooms: Optional[list[int]] = None
    include_dining_room: bool = Field(
        default=True,
        description="False only when the user explicitly asked to drop the separate dining room "
                    "(e.g. folded into the living room). Living Room and Kitchen are always required.")

    def is_ready_for_layout(self) -> bool:
        return (
            self.plot_width_ft is not None
            and self.plot_length_ft is not None
            and self.num_bedrooms is not None
            and self.num_bathrooms is not None
        )


class AdjacencyPreference(BaseModel):
    a: str
    b: str


class PartitionNode(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=60)
    room_key: Optional[str] = None
    axis: Optional[Literal["x", "y"]] = None
    fraction: Optional[float] = Field(default=None, ge=0.05, le=0.95)
    first: Optional[str] = None
    second: Optional[str] = None


class LayoutProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=80)
    root: str = Field(min_length=1, max_length=60)
    nodes: list[PartitionNode] = Field(min_length=1, max_length=63)


class DesignHints(BaseModel):
    """AI arrangement proposals plus fallback preferences; Python owns every
    coordinate, sizing and acceptance decision.

    layout_proposals are up to three distinct room-partition trees; each is
    compiled, sized and validated by `ai_arrangements.py` before it can
    become a layout. The remaining fields are only consulted when no
    proposal validates and `layout_solver.py` builds the standard fallback:
    room_weights keys are free-text room names/categories (matched loosely,
    case-insensitively) mapped to a relative size multiplier.
    adjacency_preferences are a soft, best-effort ask (e.g. keep kitchen next
    to dining) — never required for the layout to validate. attached_bathrooms
    is the one hint that changes solver STRUCTURE, not just column order: how
    many bathrooms the user wants ensuite to a bedroom rather than shared -
    the solver tries to honor it, falling back to a shared bathroom only if
    no plot arrangement can fit an attached one.
    """
    model_config = ConfigDict(allow_inf_nan=False)
    room_weights: dict[str, float] = Field(default_factory=dict)
    adjacency_preferences: list[AdjacencyPreference] = Field(default_factory=list)
    attached_bathrooms: int = Field(default=0, ge=0)
    planning_notes: list[str] = Field(default_factory=list, max_length=6)
    layout_proposals: list[LayoutProposal] = Field(default_factory=list, max_length=3)


class Door(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    wall: Wall
    position: float = Field(default=0.5, ge=0.0, le=1.0, description="0=start of wall, 1=end of wall")
    connects_to: Optional[str] = Field(default=None, description="room id on the other side, or 'outside'")
    width: float = Field(default=2.8, gt=0)
    kind: Literal["door", "opening"] = "door"


class Window(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    wall: Wall
    position: float = Field(default=0.5, ge=0, le=1,
                            description="Normalized fraction of the wall: 0=start, 0.5=center, 1=end")
    width: float = Field(default=3.0, gt=0)


class Room(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    x: float
    y: float
    width: float = Field(gt=0)
    length: float = Field(gt=0)
    floor: int = Field(default=0, ge=0)
    doors: list[Door] = Field(default_factory=list)
    windows: list[Window] = Field(default_factory=list)
    ensuite_for: Optional[str] = None


class Layout(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    version: int = 1
    plot_width_ft: float = Field(gt=0)
    plot_length_ft: float = Field(gt=0)
    planning_notes: list[str] = Field(default_factory=list, max_length=6)
    rooms: list[Room]
    wall_colors: dict[str, str] = Field(default_factory=dict)
    design_name: str = "Standard layout"
    planning_source: Literal["ai_arrangement", "standard_solver"] = "standard_solver"


class ValidationIssue(BaseModel):
    room_id: Optional[str] = None
    message: str


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SessionState(BaseModel):
    session_id: str
    project_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    history: list[ChatTurn] = Field(default_factory=list)
    spec: HouseSpec = Field(default_factory=HouseSpec)
    layout: Optional[Layout] = None
    layout_history: list[Layout] = Field(default_factory=list)
    model_3d_version: Optional[int] = None
    layout_options: list[Layout] = Field(default_factory=list)
    revision_specs: dict[int, HouseSpec] = Field(default_factory=dict)
    revision_parents: dict[int, Optional[int]] = Field(default_factory=dict)
