import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from harness.judge import ROOT, load_cases

CASE = load_cases(ROOT / "tests/data/public_samples.json")[0]
CLIENT = TestClient(app,raise_server_exceptions=False)


@pytest.mark.parametrize("mutation",[
    lambda p: p.update(operator_notes=[]),
    lambda p: p.update(operator_notes=["one"]*4),
    lambda p: p.update(operator_notes=["   "]),
    lambda p: p.update(operator_notes=[None]),
    lambda p: p["hours"].pop(),
    lambda p: p["hours"][0].update(hour=1),
    lambda p: p["hours"][0].update(hour=24),
    lambda p: p["hours"][0].update(demand_kwh=-1),
    lambda p: p["hours"][0].pop("solar_kwh"),
    lambda p: p["battery"].pop("capacity_kwh"),
])
def test_structural_rejection_and_recovery(mutation):
    payload=copy.deepcopy(CASE["input"])
    mutation(payload)
    response=CLIENT.post("/optimize-energy",json=payload)
    assert response.status_code == 400
    assert isinstance(response.json(),dict)
    assert CLIENT.get("/health").json() == {"status":"ok"}


@pytest.mark.release
@pytest.mark.parametrize("mutation",[
    lambda p: p["hours"][0].update(demand_kwh=float("inf")),
    lambda p: p["hours"][0].update(tariff_bdt_per_kwh=float("nan")),
    lambda p: p["battery"].update(initial_energy_kwh=p["battery"]["capacity_kwh"]+1),
    lambda p: p["battery"].update(minimum_energy_kwh=p["battery"]["initial_energy_kwh"]+1),
    lambda p: p["hours"][1].update(hour=True),
])
def test_invalid_numeric_input_is_rejected(mutation):
    payload=copy.deepcopy(CASE["input"])
    mutation(payload)
    response=CLIENT.post("/optimize-energy",content=json.dumps(payload),headers={"Content-Type":"application/json"})
    assert response.status_code in (400,422), response.text
    assert CLIENT.get("/health").status_code == 200
