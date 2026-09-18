"""
Local replica of the organizer's judge.  OWNER: Member D.

Run this against a live base URL and it prints a score in the same seven
categories the organizers use, so the team can see the scoreboard before the
organizers do.

    python -m harness.judge --base-url http://localhost:8000
    python -m harness.judge --base-url https://<deployed> --repeat 3

What it must check per case
---------------------------
interpretation (25):
    one entry per note, note_index order, applies semantics, directive_type,
    hours set equality, numeric values within 0.01
application + validity (25):
    replay hourly_plan against the *expected* directives from the sample pack
    (not the team's own interpretation) exactly as app.verifier does
optimization (10):
    min(1, reference_cost / recalculated_team_cost), averaged
schema (10):
    required fields present, correct types, scenario_id echoed, 24 hours
performance (10):
    p95 latency of POST /optimize-energy, failure rate, /health readiness,
    malformed-input handling (send bad JSON, expect 400 and a live service)

Reuse app.verifier for the replay so the harness and the service cannot drift
apart, and load expectations from tests/data/public_samples.json.
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="GridWise local judge")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--cases", default="tests/data/public_samples.json")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.parse_args()
    raise NotImplementedError("TODO(Member D): see docs/team/MEMBER-D-testing-deploy-docs.md")


if __name__ == "__main__":
    raise SystemExit(main())
