"""Data models for material estimation."""
from __future__ import annotations

from pydantic import BaseModel, Field


class EstimatePreferences(BaseModel):
    city_or_postcode: str = Field(min_length=1, max_length=120)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    preferred_brands: list[str] = Field(default_factory=list)
    purchase_type: str = Field(default="retail", pattern=r"^(retail|wholesale)$")
    include_delivery: bool = False


class MaterialQuantity(BaseModel):
    material: str
    quantity: float = Field(ge=0)
    unit: str
    basis: str


class PriceQuote(BaseModel):
    material: str
    unit_price: float = Field(ge=0)
    unit: str
    currency: str
    supplier: str
    source_url: str
    checked_at: str
    includes_delivery: bool = False


class EstimateResult(BaseModel):
    preferences: EstimatePreferences
    quantities: list[MaterialQuantity] = Field(default_factory=list)
    prices: list[PriceQuote] = Field(default_factory=list)
    total_material_cost: float = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
