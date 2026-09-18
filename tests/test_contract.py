"""
Contract smoke tests. These run offline and must stay green from minute zero —
they are what tells the team that `main` is not broken.

OWNER: Member D (extend with test_samples.py / test_guardrails.py).
"""

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import DIRECTIVE_TYPES, ScenarioRequest

SAMPLES = pathlib.Path(__file__).parent / "data" / "public_samples.json"
client = TestClient(app, raise_server_exceptions=False)


def load_cases():
    return json.load(open(SAMPLES))["cases"]


def test_health_is_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_malformed_json_is_400():
    r = client.post(
        "/optimize-energy",
        content='{"scenario_id": ',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_structurally_invalid_request_is_400():
    r = client.post("/optimize-energy", json={"scenario_id": "x"})
    assert r.status_code == 400


def test_service_survives_a_bad_request():
    client.post("/optimize-energy", json={"nope": True})
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_every_public_sample_parses_as_a_valid_request(case):
    req = ScenarioRequest.model_validate(case["input"])
    assert len(req.hours) == 24
    assert [h.hour for h in req.ordered_hours()] == list(range(24))
    assert 1 <= len(req.operator_notes) <= 3


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_expected_interpretations_obey_the_invariants(case):
    """Guards our reading of the spec against the organizers' own answers."""
    entries = case["expected_output"]["directive_interpretation"]
    notes = case["input"]["operator_notes"]

    assert len(entries) == len(notes)
    assert [e["note_index"] for e in entries] == list(range(len(notes)))

    for e in entries:
        assert e["directive_type"] in DIRECTIVE_TYPES
        assert e["applies"] == (e["directive_type"] != "no_op")
        if e["directive_type"] == "no_op":
            assert e["structured_adjustment"] is None
        else:
            adj = e["structured_adjustment"]
            hours = adj["hours"]
            assert hours == sorted(set(hours))
            assert all(isinstance(h, int) and 0 <= h <= 23 for h in hours)
            if e["directive_type"] == "solar_reduction":
                assert 0.0 <= adj["factor"] <= 1.0
