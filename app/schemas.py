"""
FROZEN CONTRACT — do not change during the round without telling the whole team.

Every module in this repo talks through the types in this file. Anindya owns the
HTTP layer, Ninad owns interpretation, Kabya owns guardrails + optimizer,
Fayek owns testing/deploy — but all four import from here.

Field names and value shapes mirror the Problem Statement exactly. If this file
and the Problem Statement ever disagree, the Problem Statement wins and this file
must be fixed (announce it in the team channel first).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HORIZON = 24

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]

DIRECTIVE_TYPES: tuple[str, ...] = (
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
)

BatteryAction = Literal["charge", "discharge", "idle"]

# Absolute tolerance used by the judge for kWh and BDT comparisons.
TOL = 0.01


# --------------------------------------------------------------------------
# Request  (POST /optimize-energy body)
# --------------------------------------------------------------------------


class HourInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Field(ge=0)
    solar_kwh: float = Field(ge=0)
    tariff_bdt_per_kwh: float = Field(ge=0)


class BatteryInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capacity_kwh: float = Field(ge=0)
    initial_energy_kwh: float = Field(ge=0)
    minimum_energy_kwh: float = Field(ge=0)
    max_charge_kwh_per_hour: float = Field(ge=0)
    max_discharge_kwh_per_hour: float = Field(ge=0)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(min_length=1)
    operator_notes: List[str] = Field(min_length=1, max_length=3)
    hours: List[HourInput] = Field(min_length=HORIZON, max_length=HORIZON)
    battery: BatteryInput

    @field_validator("operator_notes")
    @classmethod
    def _notes_non_empty(cls, v: List[str]) -> List[str]:
        if any(not isinstance(n, str) or not n.strip() for n in v):
            raise ValueError("operator_notes entries must be non-empty strings")
        return v

    @model_validator(mode="after")
    def _hours_cover_full_day(self) -> "ScenarioRequest":
        seen = sorted(h.hour for h in self.hours)
        if seen != list(range(HORIZON)):
            raise ValueError("hours must contain exactly one entry for each hour 0..23")
        return self

    def ordered_hours(self) -> List[HourInput]:
        """Hours sorted by `hour`, regardless of the order they arrived in."""
        return sorted(self.hours, key=lambda h: h.hour)


# --------------------------------------------------------------------------
# Response  (POST /optimize-energy 200 body)
# --------------------------------------------------------------------------


class DirectiveInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_index: int = Field(ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[Dict[str, Any]] = None
    explanation: str


class HourPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hour: int = Field(ge=0, le=23)
    grid_kwh: float = Field(ge=0)
    solar_used_kwh: float = Field(ge=0)
    battery_action: BatteryAction
    battery_kwh: float = Field(ge=0)
    battery_energy_after_kwh: float = Field(ge=0)


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourPlan]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    """Controlled error body. Never carries a stack trace or any secret."""

    error: str
    detail: Optional[str] = None


# --------------------------------------------------------------------------
# Internal types  (LLM -> guardrails -> optimizer)
# --------------------------------------------------------------------------


@dataclass
class Directive:
    """One validated directive, ready for the optimizer.

    `adjustment` is already normalised by the guardrails:
      solar_reduction          -> {"hours": [int], "factor": float}
      minimum_battery_reserve  -> {"hours": [int], "minimum_energy_kwh": float}
      no_charge_window         -> {"hours": [int]}
      no_discharge_window      -> {"hours": [int]}
      max_grid_window          -> {"hours": [int], "max_grid_kwh": float}
      no_op                    -> None
    """

    note_index: int
    directive_type: str
    adjustment: Optional[Dict[str, Any]]
    explanation: str
    source: str = "llm"  # "llm" | "llm_repaired" | "fallback" | "safe_default"

    @property
    def applies(self) -> bool:
        return self.directive_type != "no_op"

    def to_interpretation(self) -> DirectiveInterpretation:
        return DirectiveInterpretation(
            note_index=self.note_index,
            applies=self.applies,
            directive_type=self.directive_type,  # type: ignore[arg-type]
            structured_adjustment=self.adjustment if self.applies else None,
            explanation=self.explanation,
        )


@dataclass
class ConstraintSet:
    """The deterministic, per-hour effect of every applied directive.

    Built by `app.guardrails.build_constraint_set`. Consumed by the optimizer and
    by the verifier. Nothing downstream of this ever looks at raw note text.
    """

    effective_solar_kwh: List[float]
    min_energy_kwh: List[float]          # per-hour floor on battery_energy_after_kwh
    max_grid_kwh: List[Optional[float]]  # None = uncapped
    charge_blocked: List[bool]
    discharge_blocked: List[bool]
    applied: List[Directive] = field(default_factory=list)
