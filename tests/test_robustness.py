import copy

import pytest

from app import optimizer, verifier
from app.schemas import ScenarioRequest
from harness.judge import evaluate_case, expected_constraints
from harness.stress_cases import cases


@pytest.mark.parametrize("case",cases(),ids=lambda c:c["id"])
def test_synthetic_oracle_is_valid(case):
    result = evaluate_case(case,case["expected_output"])
    assert result["valid"], result
    assert result["quality_ratio"] == 1


@pytest.mark.parametrize("case",cases(),ids=lambda c:c["id"])
def test_solver_boundaries(case,require_implemented):
    require_implemented(optimizer.solve,verifier.verify)
    req = ScenarioRequest.model_validate(case["input"])
    constraints = expected_constraints(req,case["expected_output"]["directive_interpretation"])
    plan = optimizer.solve(req.ordered_hours(),req.battery,constraints)
    assert verifier.verify(plan,req.ordered_hours(),req.battery,constraints) == []
    cost = sum(p.grid_kwh * req.ordered_hours()[p.hour].tariff_bdt_per_kwh for p in plan)
    assert cost == pytest.approx(case["expected_output"]["total_cost_bdt"],abs=.01,rel=0)


def test_absolute_not_relative_tolerance():
    case = cases()[2]
    for h in case["input"]["hours"]: h["demand_kwh"] *= 1e7; h["solar_kwh"] *= 1e7
    output = copy.deepcopy(case["expected_output"])
    for row in output["hourly_plan"]:
        row["grid_kwh"] *= 1e7
        row["solar_used_kwh"] *= 1e7
    for k in ("total_cost_bdt","total_grid_kwh","peak_grid_kwh"): output[k] *= 1e7
    assert evaluate_case(case,output)["valid"]
    output["total_cost_bdt"] += 1
    assert not evaluate_case(case,output)["valid"]


def test_zero_magnitude_charge_is_legal_in_canonical_pdf():
    # PDF only says idle => amount zero, not the converse stated in team notes.
    case = cases()[0]
    output = copy.deepcopy(case["expected_output"])
    output["hourly_plan"][0]["battery_action"] = "charge"
    req = ScenarioRequest.model_validate(case["input"])
    from harness.judge import replay
    assert not any(replay(req,output,expected_constraints(req,output["directive_interpretation"])).values())
