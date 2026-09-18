"""Mutation tests prove the judge rejects plausible but wrong submissions."""
import copy
import json

import httpx
import pytest

from harness import judge

CASES = judge.load_cases(judge.ROOT / "tests/data/public_samples.json")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_reference_schedules_pass_independent_replay(case):
    result = judge.evaluate_case(case, case["expected_output"])
    assert result["valid"], result
    assert result["quality_ratio"] == 1
    assert all(v == 1 for v in result["interpretation"].values())


@pytest.mark.parametrize("mutation", [
    lambda o: o.update(scenario_id="wrong"),
    lambda o: o.update(total_cost_bdt=float("nan")),
    lambda o: o.update(total_cost_bdt=float("inf")),
    lambda o: o.update(total_cost_bdt=True),
    lambda o: o.update(total_cost_bdt="100"),
    lambda o: o.update(total_cost_bdt=o["total_cost_bdt"] + 1),
    lambda o: o["hourly_plan"].pop(),
    lambda o: o["hourly_plan"][0].update(hour=1),
    lambda o: o["hourly_plan"][0].update(grid_kwh=-1),
    lambda o: o["hourly_plan"][0].update(grid_kwh=float("inf")),
    lambda o: o["hourly_plan"][0].update(grid_kwh=o["hourly_plan"][0]["grid_kwh"] + 1),
    lambda o: o["hourly_plan"][23].update(battery_energy_after_kwh=0),
    lambda o: o["directive_interpretation"][0].update(note_index=True),
    lambda o: o["directive_interpretation"][0].update(applies=False),
    lambda o: o["directive_interpretation"][0]["structured_adjustment"].update(hours=[12, 12]),
    lambda o: o["directive_interpretation"][0]["structured_adjustment"].update(hours=[13, 12]),
    lambda o: o["directive_interpretation"][0]["structured_adjustment"].update(hours=[True, 12]),
    lambda o: o["directive_interpretation"][0]["structured_adjustment"].update(factor=float("nan")),
    lambda o: o["directive_interpretation"][0]["structured_adjustment"].update(extra=1),
    lambda o: o["directive_interpretation"][1].pop("structured_adjustment"),
])
def test_mutations_lose_validity_and_cost_credit(mutation):
    case = CASES[0]
    output = copy.deepcopy(case["expected_output"])
    mutation(output)
    result = judge.evaluate_case(case, output)
    assert not result["valid"], result
    assert result["quality_ratio"] == 0


def test_ground_truth_not_reported_interpretation_controls_replay():
    case = copy.deepcopy(CASES[0])
    # Change the expected constraint only. A judge trusting the response misses it.
    case["expected_output"]["directive_interpretation"][0]["structured_adjustment"]["factor"] = 0
    result = judge.evaluate_case(case, CASES[0]["expected_output"])
    assert not result["valid"]
    assert result["violations"]["directives"]


def test_plan_order_is_not_an_unpublished_requirement():
    output = copy.deepcopy(CASES[0]["expected_output"])
    output["hourly_plan"].reverse()
    assert judge.evaluate_case(CASES[0], output)["valid"]


@pytest.mark.parametrize("reference,cost,valid,want", [(0, 0, True, 1), (0, .01, True, 1), (0, .02, True, 0), (10, 20, True, .5), (10, 5, True, 1), (10, 10, False, 0)])
def test_cost_ratio_zero_and_invalid_cases(reference, cost, valid, want):
    assert judge.quality_ratio(reference, cost, valid) == want


@pytest.mark.parametrize("text", ['{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"x":1,"x":2}'])
def test_nonstandard_json_is_rejected(text):
    with pytest.raises(ValueError): judge.strict_json(text)


@pytest.mark.asyncio
async def test_http_judge_keeps_failed_cases_in_denominator(monkeypatch):
    monkeypatch.setattr(judge.verifier, "verify", lambda *args: [])
    def handler(request):
        if request.url.path == "/health": return httpx.Response(200, json={"status": "ok"})
        try: body = json.loads(request.content)
        except ValueError: return httpx.Response(400, json={"error": "invalid_request"})
        if "hours" not in body: return httpx.Response(400, json={"error": "invalid_request"})
        if body["scenario_id"] == CASES[1]["id"]: return httpx.Response(500, json={"error": "internal_error"})
        return httpx.Response(200, json=CASES[0]["expected_output"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        report = await judge.run_judge(client, CASES[:2], repeat=2)
    assert report["valid_cases"] == 2
    assert report["requests"] == 4
    assert report["categories"][2]["points"] == 5
    assert report["failure_rate"] == .5
    assert report["categories"][5]["points"] is None
    assert not report["ready"]


@pytest.mark.asyncio
async def test_unreachable_service_produces_report_not_crash():
    def fail(request): raise httpx.ConnectError("private detail must not be reported")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail), base_url="http://test") as client:
        report = await judge.run_judge(client, CASES[:1])
    assert report["failure_rate"] == 1
    assert report["measured_points_out_of_100"] == 0
    assert "private detail" not in json.dumps(report)


def test_shared_verifier_rejection_is_not_ignored(monkeypatch):
    monkeypatch.setattr(judge.verifier, "verify", lambda *args: ["rejected"])
    result = judge.evaluate_case(CASES[0], CASES[0]["expected_output"])
    assert not result["valid"] and result["shared_verifier"] == "rejected"


def test_arithmetic_overflow_is_a_failure_not_a_judge_crash():
    output = copy.deepcopy(CASES[0]["expected_output"])
    for row in output["hourly_plan"]: row["grid_kwh"] = 1e308
    result = judge.evaluate_case(CASES[0], output)
    assert not result["valid"]
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize("kind", ["no_charge_window", "no_discharge_window", "minimum_battery_reserve", "max_grid_window"])
def test_each_hard_directive_is_replayed_from_ground_truth(kind):
    case=copy.deepcopy(CASES[0])
    output=copy.deepcopy(case["expected_output"])
    plan=output["hourly_plan"]
    if kind in ("no_charge_window", "no_discharge_window"):
        action="charge" if kind == "no_charge_window" else "discharge"
        row=next(p for p in plan if p["battery_action"] == action and p["battery_kwh"] > .01)
        adj={"hours":[row["hour"]]}
    elif kind == "max_grid_window":
        row=next(p for p in plan if p["grid_kwh"] > 1)
        adj={"hours":[row["hour"]], "max_grid_kwh":row["grid_kwh"]-1}
    else:
        row=next(p for p in plan if p["battery_energy_after_kwh"] < case["input"]["battery"]["capacity_kwh"]-1)
        adj={"hours":[row["hour"]], "minimum_energy_kwh":row["battery_energy_after_kwh"]+1}
    case["expected_output"]["directive_interpretation"][1].update(applies=True,directive_type=kind,structured_adjustment=adj)
    result=judge.evaluate_case(case,output)
    assert not result["valid"] and result["quality_ratio"] == 0
    assert result["violations"]["directives"]
