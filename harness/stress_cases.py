"""Feasible synthetic boundary cases with analytically optimal idle schedules.

Constant tariff + no excess solar + terminal neutrality prove cost optimality:
sum(grid) >= sum(demand - effective_solar), attained by every reference here.
These supplement, and never claim to reproduce, the unpublished hidden tests.
"""
import argparse
import json
from pathlib import Path

from app.schemas import ScenarioRequest
from harness.judge import ROOT, expected_constraints


def make_case(name, note, kind="no_op", adjustment=None, *, demand=100, solar=20,
              tariff=7, capacity=200, initial=100, reserve=40, rate=50):
    request = {
        "scenario_id": name, "operator_notes": [note],
        "hours": [{"hour":h,"demand_kwh":demand,"solar_kwh":solar,"tariff_bdt_per_kwh":tariff} for h in range(24)],
        "battery": {"capacity_kwh":capacity,"initial_energy_kwh":initial,
                    "minimum_energy_kwh":reserve,"max_charge_kwh_per_hour":rate,"max_discharge_kwh_per_hour":rate},
    }
    directives = [{"note_index":0,"directive_type":kind,"applies":kind != "no_op",
                   "structured_adjustment":adjustment,"explanation":"Synthetic ground truth"}]
    constraints = expected_constraints(ScenarioRequest.model_validate(request), directives)
    plan = [{"hour":h,"grid_kwh":demand-constraints.effective_solar_kwh[h],
             "solar_used_kwh":constraints.effective_solar_kwh[h], "battery_action":"idle",
             "battery_kwh":0,"battery_energy_after_kwh":initial} for h in range(24)]
    grid = sum(p["grid_kwh"] for p in plan)
    return {"id":name,"input":request,"expected_output":{
        "scenario_id":name,"directive_interpretation":directives,"hourly_plan":plan,
        "total_grid_kwh":grid,"total_cost_bdt":grid*tariff,
        "peak_grid_kwh":max(p["grid_kwh"] for p in plan),"plan_summary":"Analytical flat-tariff optimum."}}


def cases():
    items = [
        make_case("ZERO-ENERGY", "The cafeteria menu changes tomorrow.", demand=0,solar=0,capacity=0,initial=0,reserve=0,rate=0),
        make_case("ZERO-TARIFF", "Tomorrow's cafeteria menu is unchanged.", tariff=0),
        make_case("NO-BATTERY", "The library changes its logo next month.", capacity=0,initial=0,reserve=0,rate=0),
        make_case("LOCKED-BATTERY", "The library changes its logo next month.", rate=0),
        make_case("FULL-RESERVE", "The library changes its logo next month.", initial=200,reserve=200),
        make_case("SMALL-DECIMALS", "The library changes its logo next month.", demand=.125,solar=.025,tariff=.125,capacity=.5,initial=.25,reserve=.125,rate=.05),
        make_case("WRAP-NO-CHARGE", "Do not charge the battery from 10 PM until 2 AM.", "no_charge_window", {"hours":[0,1,22,23]}),
        make_case("MIDNIGHT-DISCHARGE", "Do not discharge the battery from midnight until 1 AM.", "no_discharge_window", {"hours":[0]}),
        make_case("NOON-SOLAR-ZERO", "Solar output is unavailable from noon until 1 PM.", "solar_reduction", {"hours":[12],"factor":0}),
        make_case("SOLAR-REMAINDER", "Expect an 80 percent reduction in solar output from 1 PM to 3 PM.", "solar_reduction", {"hours":[13,14],"factor":.2}),
        make_case("SOLAR-UNCHANGED", "Use 100 percent of forecast solar from 11 AM to noon.", "solar_reduction", {"hours":[11],"factor":1}),
        make_case("CAP-ZERO", "No grid import is permitted from 6 PM until 9 PM.", "max_grid_window", {"hours":[18,19,20],"max_grid_kwh":0}, demand=20,solar=20),
        make_case("CAP-EXACT", "Keep grid imports at or below 80 kWh per hour from 6 PM until 9 PM.", "max_grid_window", {"hours":[18,19,20],"max_grid_kwh":80}),
        make_case("RESERVE-PERCENT", "Keep half of the battery capacity in reserve from 7 PM until 10 PM.", "minimum_battery_reserve", {"hours":[19,20,21],"minimum_energy_kwh":100}),
        make_case("RESERVE-ONE-KWH", "Keep at least 1 kWh in the battery from 7 PM until 10 PM.", "minimum_battery_reserve", {"hours":[19,20,21],"minimum_energy_kwh":1}),
    ]
    reversed_case = make_case("UNSORTED-INPUT", "The cafeteria menu changes tomorrow.")
    reversed_case["input"]["hours"].reverse()
    items.append(reversed_case)
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT / "harness/reports/stress_cases.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({"_meta":{"source":"team-authored synthetic stress cases"},"cases":cases()},indent=2)+"\n",encoding="utf-8")
    print(args.output)


if __name__ == "__main__": main()
