"""Executable Lane C contract, including malformed model output."""
import math

import pytest

from app import guardrails as g
from app.schemas import BatteryInput, Directive, ScenarioRequest
from harness.judge import ROOT, load_cases

BATTERY = BatteryInput(capacity_kwh=200, initial_energy_kwh=100,
                       minimum_energy_kwh=40, max_charge_kwh_per_hour=50,
                       max_discharge_kwh_per_hour=50)


@pytest.mark.parametrize("raw,expected", [([14,13], [13,14]), ([13,13,14], [13,14]), (["13",14.0], [13,14]), ([13,25,14], [13,14]), (13,[13]), ([],[]), (None,[]), ("afternoon",[]), ([True,13.5,float("nan"),float("inf")],[])])
def test_hours(raw, expected, require_implemented):
    require_implemented(g.normalise_hours)
    assert g.normalise_hours(raw) == expected


@pytest.mark.parametrize("raw,expected", [(.2,.2),(20,.2),("20%",.2),(1,1),(-.1,None),(1.4,None),("a lot",None),(float("nan"),None),(float("inf"),None),(True,None)])
def test_factor(raw, expected, require_implemented):
    require_implemented(g.normalise_factor)
    result = g.normalise_factor(raw)
    assert result == pytest.approx(expected) if expected is not None else result is None


@pytest.mark.parametrize("kind,adjustment", [
    ("solar_reduction", {"hours":[13,14],"factor":.2}),
    ("minimum_battery_reserve", {"hours":[18],"minimum_energy_kwh":120}),
    ("no_charge_window", {"hours":[14]}), ("no_discharge_window", {"hours":[15]}),
    ("max_grid_window", {"hours":[19],"max_grid_kwh":0}), ("no_op", {}),
])
def test_roundtrip(kind, adjustment, require_implemented):
    require_implemented(g.validate_interpretations)
    result = g.validate_interpretations([dict(note_index=0, directive_type=kind, **adjustment)], ["synthetic note"], BATTERY)
    assert len(result) == 1 and result[0].note_index == 0
    assert result[0].directive_type == kind
    assert result[0].adjustment == (adjustment if kind != "no_op" else None)


@pytest.mark.parametrize("raw", [[], None, "not a list", [None], [{"directive_type":"invented"}], [{"note_index":0,"directive_type":"no_op"},{"note_index":0,"directive_type":"no_op"}]])
def test_bad_mapping_never_drops_notes(raw, require_implemented):
    require_implemented(g.validate_interpretations)
    result = g.validate_interpretations(raw, ["one", "two", "three"], BATTERY)
    assert [d.note_index for d in result] == [0,1,2]
    assert all(d.directive_type == "no_op" and d.adjustment is None for d in result)


def test_reserve_clamps_without_converting_small_kwh(require_implemented):
    require_implemented(g.validate_interpretations)
    raw = [{"directive_type":"minimum_battery_reserve", "hours":[18], "minimum_energy_kwh":n} for n in (500,1)]
    result = g.validate_interpretations(raw, ["one", "two"], BATTERY)
    assert [d.adjustment["minimum_energy_kwh"] for d in result] == [200,1]


def test_overlapping_constraints_and_unsorted_input(require_implemented):
    require_implemented(g.build_constraint_set)
    case = load_cases(ROOT / "tests/data/public_samples.json")[0]
    req = ScenarioRequest.model_validate(case["input"])
    directives = [Directive(i,t,a,"") for i,(t,a) in enumerate([
        ("solar_reduction", {"hours":[12],"factor":.5}),
        ("solar_reduction", {"hours":[12],"factor":.5}),
        ("max_grid_window", {"hours":[12],"max_grid_kwh":50}),
        ("max_grid_window", {"hours":[12],"max_grid_kwh":20}),
        ("minimum_battery_reserve", {"hours":[12],"minimum_energy_kwh":120}),
        ("no_charge_window", {"hours":[12]}),
        ("no_discharge_window", {"hours":[12]}),
    ])]
    c = g.build_constraint_set(directives, list(reversed(req.hours)), req.battery)
    assert c.effective_solar_kwh[12] == req.hours[12].solar_kwh * .25
    assert c.max_grid_kwh[12] == 20
    assert c.min_energy_kwh[12] == max(120,req.battery.minimum_energy_kwh)
    assert c.charge_blocked[12] and c.discharge_blocked[12]
