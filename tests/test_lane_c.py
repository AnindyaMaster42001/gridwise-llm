"""
Lane C tests — guardrails, optimizer, verifier.  OWNER: Kabya.

New file, deliberately named so it cannot collide with Fayek's planned
`test_guardrails.py` / `test_samples.py`. It imports only `app.guardrails`,
`app.optimizer`, `app.verifier` and `app.schemas`, so it stays green no matter
what state lanes A, B and D are in.

Covers the test list in docs/04-guardrails.md plus the definition of done in
docs/team/KABYA-lane-c-guardrails-optimizer.md.
"""

import json
import pathlib
from typing import List, Optional

import pytest

from app.guardrails import (
    build_constraint_set,
    normalise_factor,
    normalise_hours,
    validate_interpretations,
)
from app.optimizer import InfeasibleError, safe_baseline_plan, solve, solve_with_ladder
from app.schemas import TOL, BatteryInput, Directive, HourInput, HourPlan, ScenarioRequest
from app.verifier import verify

SAMPLES = pathlib.Path(__file__).parent / "data" / "public_samples.json"


def load_cases():
    return json.load(open(SAMPLES))["cases"]


BATTERY = BatteryInput(
    capacity_kwh=500,
    initial_energy_kwh=200,
    minimum_energy_kwh=50,
    max_charge_kwh_per_hour=100,
    max_discharge_kwh_per_hour=100,
)


def flat_hours(demand=100.0, solar=0.0, tariff=None) -> List[HourInput]:
    return [
        HourInput(
            hour=h,
            demand_kwh=demand,
            solar_kwh=solar,
            tariff_bdt_per_kwh=(tariff[h] if tariff else 5.0 + (h % 6)),
        )
        for h in range(24)
    ]


def directive(index: int, kind: str, adjustment, source: str = "llm") -> Directive:
    return Directive(
        note_index=index,
        directive_type=kind,
        adjustment=adjustment,
        explanation="test",
        source=source,
    )


# ==========================================================================
# Guardrails — stage 3, hours
# ==========================================================================


@pytest.mark.parametrize(
    "raw, expected",
    [
        ([14, 13], [13, 14]),                 # unsorted
        ([13, 13, 14], [13, 14]),             # duplicated
        (["13", 14.0], [13, 14]),             # strings and floats
        ([13, 25, 14], [13, 14]),             # out of range dropped, rest kept
        ([-1, 0], [0]),
        (13, [13]),                           # scalar is a one-hour window
        ("13", [13]),
        ("13,14", [13, 14]),
        ("13:00", [13]),
        ([], []),
        (None, []),
        ("afternoon", []),
        ({"start": 13, "end": 15}, [13, 14]),  # end-exclusive
        ({"start": 22, "end": 2}, [0, 1, 22, 23]),  # wraps midnight, still ascending
    ],
)
def test_normalise_hours(raw, expected):
    assert normalise_hours(raw) == expected


# ==========================================================================
# Guardrails — stage 4, numbers
# ==========================================================================


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0.2, 0.2),
        (20, 0.2),
        ("20%", 0.2),
        ("12.5%", 0.125),
        (1.0, 1.0),
        (0.0, 0.0),
        (100, 1.0),
        (1.4, None),
        (-0.1, None),
        (-1, None),
        ("a lot", None),
        (None, None),
    ],
)
def test_normalise_factor(raw, expected):
    assert normalise_factor(raw) == expected


def test_factor_direction_flips_only_on_an_explicit_matching_reduction():
    # The model emitted the reduction, not the remainder, and said so.
    assert normalise_factor(0.8, "Expect an 80% reduction in rooftop solar") == 0.2
    assert normalise_factor(0.8, "a reduction of 80% during maintenance") == 0.2
    assert normalise_factor(0.8, "solar is cut by 80 percent") == 0.2


def test_factor_direction_does_not_flip_a_correct_remainder():
    # Same wording, but the model already did the conversion. Trust the number.
    assert normalise_factor(0.2, "Expect an 80% reduction in rooftop solar") == 0.2
    # "drops to X" is a remainder, not a reduction.
    assert normalise_factor(0.25, "Solar drops to 25% during cleaning") == 0.25
    assert normalise_factor(0.2, "PV production will drop to about 20%") == 0.2
    # A bare reduction word with no matching percentage never flips.
    assert normalise_factor(0.3, "some reduction is expected") == 0.3


def test_reserve_above_capacity_clamps():
    raw = [{"note_index": 0, "directive_type": "minimum_battery_reserve",
            "hours": [18], "minimum_energy_kwh": 9000}]
    got = validate_interpretations(raw, ["n"], BATTERY)[0]
    assert got.adjustment == {"hours": [18], "minimum_energy_kwh": 500.0}


def test_suspicious_fractional_reserve_is_never_auto_converted():
    raw = [{"note_index": 0, "directive_type": "minimum_battery_reserve",
            "hours": [18], "minimum_energy_kwh": 1.0}]
    got = validate_interpretations(raw, ["n"], BATTERY)[0]
    assert got.adjustment["minimum_energy_kwh"] == 1.0  # not 500


@pytest.mark.parametrize("value", [-5, "nope", None, float("nan")])
def test_unusable_grid_cap_demotes(value):
    raw = [{"note_index": 0, "directive_type": "max_grid_window",
            "hours": [18], "max_grid_kwh": value}]
    got = validate_interpretations(raw, ["n"], BATTERY)[0]
    assert got.directive_type == "no_op"
    assert got.adjustment is None


# ==========================================================================
# Guardrails — stages 1, 2, 5
# ==========================================================================


CLEAN_RAW = [
    ({"directive_type": "solar_reduction", "hours": [13, 14], "factor": 0.2},
     {"hours": [13, 14], "factor": 0.2}),
    ({"directive_type": "minimum_battery_reserve", "hours": [18], "minimum_energy_kwh": 120},
     {"hours": [18], "minimum_energy_kwh": 120.0}),
    ({"directive_type": "no_charge_window", "hours": [14, 15]}, {"hours": [14, 15]}),
    ({"directive_type": "no_discharge_window", "hours": [2]}, {"hours": [2]}),
    ({"directive_type": "max_grid_window", "hours": [19], "max_grid_kwh": 180},
     {"hours": [19], "max_grid_kwh": 180.0}),
    ({"directive_type": "no_op"}, None),
]


@pytest.mark.parametrize("raw, expected", CLEAN_RAW)
def test_every_directive_type_round_trips(raw, expected):
    got = validate_interpretations([dict(raw, note_index=0)], ["n"], BATTERY)[0]
    assert got.directive_type == raw["directive_type"]
    assert got.adjustment == expected
    assert got.applies == (raw["directive_type"] != "no_op")


def test_nested_structured_adjustment_is_accepted():
    raw = [{"note_index": 0, "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [13, 14], "factor": 0.2}}]
    got = validate_interpretations(raw, ["n"], BATTERY)[0]
    assert got.adjustment == {"hours": [13, 14], "factor": 0.2}


def test_type_accepts_case_and_whitespace_variation():
    raw = [{"note_index": 0, "directive_type": " Solar_Reduction ", "hours": [13], "factor": 0.5}]
    assert validate_interpretations(raw, ["n"], BATTERY)[0].directive_type == "solar_reduction"


def test_unknown_directive_type_becomes_no_op():
    raw = [{"note_index": 0, "directive_type": "turn_off_the_lights", "hours": [13]}]
    got = validate_interpretations(raw, ["n"], BATTERY)[0]
    assert got.directive_type == "no_op"
    assert got.adjustment is None
    assert got.applies is False


def test_three_notes_but_only_two_entries():
    raw = [{"note_index": 0, "directive_type": "no_charge_window", "hours": [2]},
           {"note_index": 1, "directive_type": "no_op"}]
    got = validate_interpretations(raw, ["a", "b", "c"], BATTERY)
    assert [d.note_index for d in got] == [0, 1, 2]
    assert got[2].directive_type == "no_op"
    assert got[2].adjustment is None


def test_two_entries_claiming_note_index_zero():
    raw = [{"note_index": 0, "directive_type": "no_charge_window", "hours": [2]},
           {"note_index": 0, "directive_type": "no_discharge_window", "hours": [5]}]
    got = validate_interpretations(raw, ["a", "b"], BATTERY)
    assert [d.note_index for d in got] == [0, 1]
    assert got[0].directive_type == "no_charge_window"   # first claim wins
    assert got[1].directive_type == "no_discharge_window"  # rest re-map positionally


def test_missing_note_index_falls_back_to_position():
    raw = [{"directive_type": "no_charge_window", "hours": [2]},
           {"directive_type": "no_op"}]
    got = validate_interpretations(raw, ["a", "b"], BATTERY)
    assert [d.note_index for d in got] == [0, 1]
    assert got[0].directive_type == "no_charge_window"


@pytest.mark.parametrize("raw", [[], None, "garbage", {}, [None, 7], {"directives": []}])
def test_unusable_model_output_yields_all_no_op(raw):
    got = validate_interpretations(raw, ["a", "b", "c"], BATTERY)
    assert len(got) == 3
    assert [d.note_index for d in got] == [0, 1, 2]
    assert all(d.directive_type == "no_op" and d.adjustment is None and not d.applies for d in got)


def test_invariants_hold_for_every_directive_returned():
    raw = [{"note_index": 0, "directive_type": "solar_reduction", "hours": [14, 13], "factor": 20},
           {"note_index": 1, "directive_type": "nonsense"},
           {"note_index": 2, "directive_type": "no_op"}]
    for d in validate_interpretations(raw, ["a", "b", "c"], BATTERY):
        entry = d.to_interpretation()
        assert entry.applies == (entry.directive_type != "no_op")
        assert (entry.structured_adjustment is None) == (entry.directive_type == "no_op")


# ==========================================================================
# Guardrails — stage 6, combination rules
# ==========================================================================


def test_overlapping_solar_reductions_multiply():
    hours = flat_hours(solar=100.0)
    cs = build_constraint_set(
        [directive(0, "solar_reduction", {"hours": [10, 11], "factor": 0.5}),
         directive(1, "solar_reduction", {"hours": [11, 12], "factor": 0.5})],
        hours, BATTERY,
    )
    assert cs.effective_solar_kwh[10] == 50.0
    assert cs.effective_solar_kwh[11] == 25.0   # two 50% reductions
    assert cs.effective_solar_kwh[12] == 50.0
    assert cs.effective_solar_kwh[13] == 100.0


def test_overlapping_grid_caps_take_the_min():
    cs = build_constraint_set(
        [directive(0, "max_grid_window", {"hours": [18, 19], "max_grid_kwh": 180}),
         directive(1, "max_grid_window", {"hours": [19, 20], "max_grid_kwh": 150})],
        flat_hours(), BATTERY,
    )
    assert cs.max_grid_kwh[18] == 180.0
    assert cs.max_grid_kwh[19] == 150.0
    assert cs.max_grid_kwh[20] == 150.0
    assert cs.max_grid_kwh[21] is None


def test_overlapping_reserves_take_the_max_over_the_base_minimum():
    cs = build_constraint_set(
        [directive(0, "minimum_battery_reserve", {"hours": [18, 19], "minimum_energy_kwh": 120}),
         directive(1, "minimum_battery_reserve", {"hours": [19], "minimum_energy_kwh": 200})],
        flat_hours(), BATTERY,
    )
    assert cs.min_energy_kwh[17] == 50.0    # base minimum
    assert cs.min_energy_kwh[18] == 120.0
    assert cs.min_energy_kwh[19] == 200.0


def test_blocked_windows_are_any():
    cs = build_constraint_set(
        [directive(0, "no_charge_window", {"hours": [2, 3]}),
         directive(1, "no_discharge_window", {"hours": [3, 4]})],
        flat_hours(), BATTERY,
    )
    assert cs.charge_blocked[2] and cs.charge_blocked[3] and not cs.charge_blocked[4]
    assert cs.discharge_blocked[3] and cs.discharge_blocked[4] and not cs.discharge_blocked[2]


def test_no_op_directives_never_reach_the_constraint_set():
    cs = build_constraint_set([directive(0, "no_op", None)], flat_hours(solar=10.0), BATTERY)
    assert cs.applied == []
    assert cs.effective_solar_kwh == [10.0] * 24


# ==========================================================================
# Optimizer — the ten public cases
# ==========================================================================


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_public_case_reproduces_the_reference_cost(case):
    req = ScenarioRequest.model_validate(case["input"])
    hours = req.ordered_hours()
    directives = validate_interpretations(
        case["expected_output"]["directive_interpretation"], req.operator_notes, req.battery
    )
    cs = build_constraint_set(directives, hours, req.battery)
    plan = solve(hours, req.battery, cs)

    cost = sum(p.grid_kwh * h.tariff_bdt_per_kwh for p, h in zip(plan, hours))
    assert cost == pytest.approx(case["expected_output"]["total_cost_bdt"], abs=TOL)
    assert verify(plan, hours, req.battery, cs) == []


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_public_case_ends_the_day_exactly_where_it_started(case):
    req = ScenarioRequest.model_validate(case["input"])
    hours = req.ordered_hours()
    directives = validate_interpretations(
        case["expected_output"]["directive_interpretation"], req.operator_notes, req.battery
    )
    cs = build_constraint_set(directives, hours, req.battery)
    plan = solve(hours, req.battery, cs)
    assert plan[23].battery_energy_after_kwh == req.battery.initial_energy_kwh


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_guardrails_round_trip_the_organizer_interpretation(case):
    req = ScenarioRequest.model_validate(case["input"])
    expected = case["expected_output"]["directive_interpretation"]
    got = validate_interpretations(expected, req.operator_notes, req.battery)
    for directive_, want in zip(got, expected):
        assert directive_.directive_type == want["directive_type"]
        assert directive_.adjustment == want["structured_adjustment"]


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_verifier_accepts_the_organizer_reference_plan(case):
    req = ScenarioRequest.model_validate(case["input"])
    hours = req.ordered_hours()
    directives = validate_interpretations(
        case["expected_output"]["directive_interpretation"], req.operator_notes, req.battery
    )
    cs = build_constraint_set(directives, hours, req.battery)
    plan = [HourPlan.model_validate(p) for p in case["expected_output"]["hourly_plan"]]
    assert verify(plan, hours, req.battery, cs) == []


def test_plan_emits_one_action_per_hour_and_idle_means_zero():
    hours = flat_hours(solar=40.0)
    cs = build_constraint_set([], hours, BATTERY)
    plan = solve(hours, BATTERY, cs)
    assert [p.hour for p in plan] == list(range(24))
    for p in plan:
        assert p.battery_action in ("charge", "discharge", "idle")
        assert (p.battery_kwh == 0.0) == (p.battery_action == "idle")


def test_optimizer_respects_each_directive_type():
    tariff = [20.0 if 18 <= h <= 21 else 4.0 for h in range(24)]
    hours = flat_hours(demand=150.0, solar=80.0, tariff=tariff)
    directives = [
        directive(0, "solar_reduction", {"hours": [12, 13], "factor": 0.25}),
        directive(1, "no_charge_window", {"hours": [2, 3]}),
        directive(2, "no_discharge_window", {"hours": [19]}),
        directive(3, "minimum_battery_reserve", {"hours": [18, 19], "minimum_energy_kwh": 300}),
        directive(4, "max_grid_window", {"hours": [20, 21], "max_grid_kwh": 120}),
    ]
    cs = build_constraint_set(directives, hours, BATTERY)
    plan = solve(hours, BATTERY, cs)

    assert verify(plan, hours, BATTERY, cs) == []
    assert plan[12].solar_used_kwh <= 20.0 + TOL          # 80 * 0.25
    assert all(plan[h].battery_action != "charge" for h in (2, 3))
    assert plan[19].battery_action != "discharge"
    assert all(plan[h].battery_energy_after_kwh >= 300 - TOL for h in (18, 19))
    assert all(plan[h].grid_kwh <= 120 + TOL for h in (20, 21))


def test_infeasible_constraints_raise_rather_than_return_a_partial_plan():
    hours = flat_hours(demand=300.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "max_grid_window", {"hours": list(range(24)), "max_grid_kwh": 0.0})],
        hours, BATTERY,
    )
    with pytest.raises(InfeasibleError):
        solve(hours, BATTERY, cs)


# ==========================================================================
# Verifier — it must catch every violation class
# ==========================================================================


def clean_plan_and_context():
    hours = flat_hours(demand=120.0, solar=60.0)
    cs = build_constraint_set([], hours, BATTERY)
    return solve(hours, BATTERY, cs), hours, cs


def mutate(plan: List[HourPlan], hour: int, **changes) -> List[HourPlan]:
    return [p.model_copy(update=changes) if p.hour == hour else p for p in plan]


def test_verifier_accepts_a_clean_plan():
    plan, hours, cs = clean_plan_and_context()
    assert verify(plan, hours, BATTERY, cs) == []


def test_verifier_catches_a_broken_energy_balance():
    plan, hours, cs = clean_plan_and_context()
    broken = mutate(plan, 5, grid_kwh=plan[5].grid_kwh + 25.0)
    assert any("energy balance" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_a_battery_bound_violation():
    plan, hours, cs = clean_plan_and_context()
    broken = mutate(plan, 5, battery_energy_after_kwh=1e6)
    assert any("above capacity" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_a_reserve_violation():
    hours = flat_hours(demand=120.0, solar=60.0)
    cs = build_constraint_set(
        [directive(0, "minimum_battery_reserve", {"hours": [18], "minimum_energy_kwh": 400})],
        hours, BATTERY,
    )
    plan = solve(hours, BATTERY, cs)
    broken = mutate(plan, 18, battery_energy_after_kwh=60.0)
    assert any("below required minimum" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_a_rate_limit_violation():
    plan, hours, cs = clean_plan_and_context()
    broken = mutate(plan, 5, battery_action="charge", battery_kwh=400.0)
    assert any("exceeds max_charge" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_a_broken_state_transition():
    plan, hours, cs = clean_plan_and_context()
    broken = mutate(plan, 5, battery_energy_after_kwh=plan[5].battery_energy_after_kwh + 7.0)
    assert any("does not follow" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_solar_overuse():
    plan, hours, cs = clean_plan_and_context()
    broken = mutate(plan, 5, solar_used_kwh=plan[5].solar_used_kwh + 500.0)
    assert any("exceeds effective solar" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_a_grid_cap_violation():
    hours = flat_hours(demand=120.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "max_grid_window", {"hours": [20], "max_grid_kwh": 50})], hours, BATTERY
    )
    plan = solve(hours, BATTERY, cs)
    broken = mutate(plan, 20, grid_kwh=200.0)
    assert any("exceeds max_grid_window cap" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_a_blocked_window_violation():
    hours = flat_hours(demand=120.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "no_charge_window", {"hours": [4]})], hours, BATTERY
    )
    plan = solve(hours, BATTERY, cs)
    broken = mutate(plan, 4, battery_action="charge", battery_kwh=10.0)
    assert any("no_charge_window" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_a_no_discharge_violation():
    hours = flat_hours(demand=120.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "no_discharge_window", {"hours": [4]})], hours, BATTERY
    )
    plan = solve(hours, BATTERY, cs)
    broken = mutate(plan, 4, battery_action="discharge", battery_kwh=10.0)
    assert any("no_discharge_window" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_idle_with_a_non_zero_magnitude():
    plan, hours, cs = clean_plan_and_context()
    broken = mutate(plan, 5, battery_action="idle", battery_kwh=12.0)
    assert any("idle but battery_kwh" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_broken_end_of_day_neutrality():
    plan, hours, cs = clean_plan_and_context()
    broken = mutate(plan, 23, battery_action="discharge", battery_kwh=40.0,
                    battery_energy_after_kwh=plan[22].battery_energy_after_kwh - 40.0,
                    grid_kwh=max(0.0, plan[23].grid_kwh - 40.0))
    assert any("does not return to the initial" in v for v in verify(broken, hours, BATTERY, cs))


def test_verifier_catches_a_missing_hour():
    plan, hours, cs = clean_plan_and_context()
    assert any("missing hours" in v for v in verify(plan[:-1], hours, BATTERY, cs))


def test_verifier_catches_a_negative_value():
    plan, hours, cs = clean_plan_and_context()
    broken = mutate(plan, 5, grid_kwh=-10.0)
    assert any("negative" in v for v in verify(broken, hours, BATTERY, cs))


# ==========================================================================
# Safe baseline and the infeasibility ladder
# ==========================================================================


def test_safe_baseline_is_valid_with_no_directives():
    hours = flat_hours(demand=120.0, solar=60.0)
    cs = build_constraint_set([], hours, BATTERY)
    plan = safe_baseline_plan(hours, BATTERY, cs)
    assert verify(plan, hours, BATTERY, cs) == []


def test_safe_baseline_is_valid_under_a_grid_cap_tight_enough_to_bite():
    hours = flat_hours(demand=100.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "max_grid_window", {"hours": [18, 19], "max_grid_kwh": 60})],
        hours, BATTERY,
    )
    plan = safe_baseline_plan(hours, BATTERY, cs)
    assert verify(plan, hours, BATTERY, cs) == []
    # the cap genuinely bites: idle would have imported 100 kWh in those hours
    assert all(plan[h].grid_kwh <= 60 + TOL for h in (18, 19))
    assert any(plan[h].battery_action == "discharge" for h in (18, 19))


def test_safe_baseline_is_valid_under_a_reserve_above_the_starting_charge():
    hours = flat_hours(demand=100.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "minimum_battery_reserve",
                   {"hours": [18, 19, 20, 21], "minimum_energy_kwh": 300})],
        hours, BATTERY,
    )
    plan = safe_baseline_plan(hours, BATTERY, cs)
    assert verify(plan, hours, BATTERY, cs) == []
    assert all(plan[h].battery_energy_after_kwh >= 300 - TOL for h in (18, 19, 20, 21))


def test_safe_baseline_never_raises_on_a_hopeless_constraint_set():
    hours = flat_hours(demand=400.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "max_grid_window", {"hours": list(range(24)), "max_grid_kwh": 0.0})],
        hours, BATTERY,
    )
    plan = safe_baseline_plan(hours, BATTERY, cs)   # must not raise
    assert len(plan) == 24


def test_ladder_uses_every_directive_when_the_scenario_is_feasible():
    hours = flat_hours(demand=120.0, solar=50.0)
    directives = [directive(0, "no_charge_window", {"hours": [2, 3]})]
    outcome = solve_with_ladder(hours, BATTERY, directives)
    assert outcome.rung == "all_directives"
    assert outcome.dropped_note_indices == []
    assert verify(outcome.plan, hours, BATTERY, outcome.constraints) == []


def test_ladder_degrades_contradictory_directives_to_a_valid_plan():
    # A zero grid cap all day with no solar and no discharge allowed is not
    # satisfiable. The ladder must still hand back a plan that verifies.
    hours = flat_hours(demand=200.0, solar=0.0)
    directives = [
        directive(0, "max_grid_window", {"hours": list(range(24)), "max_grid_kwh": 0.0}, source="fallback"),
        directive(1, "no_discharge_window", {"hours": list(range(24))}),
    ]
    outcome = solve_with_ladder(hours, BATTERY, directives)
    assert outcome.rung != "all_directives"
    assert verify(outcome.plan, hours, BATTERY, outcome.constraints) == []


def test_ladder_drops_the_least_trustworthy_directive_first():
    hours = flat_hours(demand=200.0, solar=0.0)
    directives = [
        directive(0, "no_charge_window", {"hours": list(range(24))}, source="llm"),
        directive(1, "max_grid_window", {"hours": list(range(24)), "max_grid_kwh": 0.0}, source="fallback"),
    ]
    outcome = solve_with_ladder(hours, BATTERY, directives)
    assert outcome.dropped_note_indices[:1] == [1]   # the fallback one went first
    assert verify(outcome.plan, hours, BATTERY, outcome.constraints) == []


def test_ladder_always_returns_twenty_four_hours():
    hours = flat_hours(demand=500.0, solar=0.0)
    directives = [
        directive(0, "minimum_battery_reserve", {"hours": list(range(24)), "minimum_energy_kwh": 500}),
        directive(1, "no_charge_window", {"hours": list(range(24))}),
    ]
    outcome = solve_with_ladder(hours, BATTERY, directives)
    assert [p.hour for p in outcome.plan] == list(range(24))


def test_safe_baseline_pre_charges_far_enough_ahead_for_a_big_reserve():
    """Regression: a reserve needing several hours of charging was met too late.

    500 kWh at hour 12, starting from 200 with a 100 kWh/h charge limit, has to
    begin charging at hour 10. A floor applied only in the hour it bites leaves
    one hour of charging and lands at 300.
    """
    hours = flat_hours(demand=100.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "minimum_battery_reserve", {"hours": [12], "minimum_energy_kwh": 500})],
        hours, BATTERY,
    )
    plan = safe_baseline_plan(hours, BATTERY, cs)
    assert verify(plan, hours, BATTERY, cs) == []
    assert plan[12].battery_energy_after_kwh >= 500 - TOL


def test_safe_baseline_handles_a_reserve_with_charging_blocked_just_before_it():
    hours = flat_hours(demand=100.0, solar=0.0)
    cs = build_constraint_set(
        [directive(0, "minimum_battery_reserve", {"hours": [12], "minimum_energy_kwh": 400}),
         directive(1, "no_charge_window", {"hours": [10, 11]})],
        hours, BATTERY,
    )
    plan = safe_baseline_plan(hours, BATTERY, cs)
    assert verify(plan, hours, BATTERY, cs) == []


@pytest.mark.parametrize(
    "battery",
    [
        BatteryInput(capacity_kwh=0, initial_energy_kwh=0, minimum_energy_kwh=0,
                     max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0),
        BatteryInput(capacity_kwh=500, initial_energy_kwh=500, minimum_energy_kwh=50,
                     max_charge_kwh_per_hour=100, max_discharge_kwh_per_hour=100),
        BatteryInput(capacity_kwh=500, initial_energy_kwh=50, minimum_energy_kwh=50,
                     max_charge_kwh_per_hour=100, max_discharge_kwh_per_hour=100),
        BatteryInput(capacity_kwh=500, initial_energy_kwh=200, minimum_energy_kwh=50,
                     max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0),
    ],
    ids=["zero-capacity", "starts-full", "starts-empty", "immovable"],
)
def test_degenerate_batteries_still_produce_a_valid_plan(battery):
    hours = flat_hours(demand=100.0, solar=30.0)
    cs = build_constraint_set([], hours, battery)
    plan = solve(hours, battery, cs)
    assert verify(plan, hours, battery, cs) == []
    assert verify(safe_baseline_plan(hours, battery, cs), hours, battery, cs) == []
