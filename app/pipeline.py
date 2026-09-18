"""
Orchestration: notes -> LLM -> guardrails -> optimizer -> verifier -> response.
OWNER: Member A.

    run_pipeline() is the only function main.py calls. It owns the time budget:
    if interpretation is slow or the optimizer struggles, it must still return a
    *valid* response before the judge's 30 s timeout.

Required behaviour
------------------
1. Ask the interpreter for one raw directive per note (Member B).
2. Run guardrails over the raw output (Member C). Invalid entries are repaired
   where the repair is unambiguous, otherwise demoted to no_op — never dropped,
   never invented.
3. Build the ConstraintSet and solve (Member C).
4. Verify the plan against the ConstraintSet before returning (Member C). If the
   verification fails, fall back to the safe baseline plan rather than returning
   an invalid schedule.
5. Recompute total_grid_kwh / total_cost_bdt / peak_grid_kwh **from hourly_plan**,
   never from optimizer internals — the judge recalculates from the plan.
6. Emit exactly one interpretation entry per note, in note_index order.
"""

from __future__ import annotations

from typing import List

from app.schemas import (
    Directive,
    HourPlan,
    OptimizeResponse,
    ScenarioRequest,
)


async def run_pipeline(payload: ScenarioRequest, request_id: str = "") -> OptimizeResponse:
    """Full request path. Must not raise for any well-formed scenario."""
    raise NotImplementedError("TODO(Member A): see docs/team/MEMBER-A-api-orchestration.md")


def summarise_totals(plan: List[HourPlan], tariffs: List[float]) -> tuple[float, float, float]:
    """Return (total_grid_kwh, total_cost_bdt, peak_grid_kwh) recomputed from `plan`.

    `tariffs` is indexed by hour. The judge recalculates these three numbers from
    hourly_plan and compares within 0.01, so they must be derived here and
    nowhere else.
    """
    raise NotImplementedError("TODO(Member A)")


def build_plan_summary(directives: List[Directive], total_cost: float, peak: float) -> str:
    """Short human-readable strategy sentence for `plan_summary`.

    Not machine-graded, but it must be truthful about which directives were
    applied — judges read it when resolving ties.
    """
    raise NotImplementedError("TODO(Member A)")
