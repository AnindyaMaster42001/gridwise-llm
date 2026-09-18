"""Local diagnostic judge. Owner: Fayek Ahmed. Not the official hidden scorer."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

from app import verifier
from app.schemas import ConstraintSet, Directive, HourPlan, OptimizeResponse, ScenarioRequest

TOL = 0.01
ROOT = Path(__file__).resolve().parents[1]
SHAPES = {
    "solar_reduction": {"hours", "factor"},
    "minimum_battery_reserve": {"hours", "minimum_energy_kwh"},
    "no_charge_window": {"hours"}, "no_discharge_window": {"hours"},
    "max_grid_window": {"hours", "max_grid_kwh"}, "no_op": set(),
}


def number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=0, abs_tol=TOL)


def strict_json(text: str) -> Any:
    def reject(value: str) -> None:
        raise ValueError("non-finite JSON number")

    def unique(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result
    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result): raise ValueError("non-finite JSON number")
        return result
    return json.loads(text, parse_constant=reject, parse_float=finite_float, object_pairs_hook=unique)


def schema_errors(request: dict, output: Any) -> list[str]:
    errors: list[str] = []
    try:
        OptimizeResponse.model_validate(output, strict=True)
    except Exception:
        return ["response fields/types do not match OptimizeResponse"]
    if output["scenario_id"] != request["scenario_id"]:
        errors.append("scenario_id does not echo request")
    for key in ("total_grid_kwh", "total_cost_bdt", "peak_grid_kwh"):
        if not number(output[key]) or output[key] < 0:
            errors.append(f"{key}: expected finite non-negative number")
    entries = output["directive_interpretation"]
    if len(entries) != len(request["operator_notes"]):
        errors.append("one interpretation required per note")
    for i, entry in enumerate(entries):
        if type(entry["note_index"]) is not int or entry["note_index"] != i:
            errors.append(f"note {i}: invalid index/order")
        kind = entry["directive_type"]
        if "structured_adjustment" not in entry:
            errors.append(f"note {i}: missing adjustment")
        adj = entry.get("structured_adjustment")
        if entry["applies"] is not (kind != "no_op"):
            errors.append(f"note {i}: applies semantics")
        if kind == "no_op":
            if adj is not None: errors.append(f"note {i}: no_op adjustment must be null")
            continue
        if not isinstance(adj, dict) or set(adj) != SHAPES[kind]:
            errors.append(f"note {i}: wrong adjustment shape")
            continue
        hours = adj["hours"]
        if not isinstance(hours, list) or any(type(h) is not int or not 0 <= h < 24 for h in hours):
            errors.append(f"note {i}: invalid hours")
        elif hours != sorted(set(hours)):
            errors.append(f"note {i}: hours must be unique and ascending")
        for key in SHAPES[kind] - {"hours"}:
            value = adj[key]
            upper = 1 if key == "factor" else request["battery"]["capacity_kwh"] if key == "minimum_energy_kwh" else math.inf
            if not number(value) or not 0 <= value <= upper:
                errors.append(f"note {i}: invalid {key}")
    plan = output["hourly_plan"]
    if len(plan) != 24 or sorted(row["hour"] for row in plan) != list(range(24)):
        errors.append("plan must cover each hour 0..23 exactly once")
    for row in plan:
        for key in ("grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh"):
            if not number(row[key]) or row[key] < 0:
                errors.append(f"hour {row['hour']}: invalid {key}")
    return errors


def load_cases(path: Path) -> list[dict]:
    cases = strict_json(path.read_text(encoding="utf-8"))["cases"]
    if not cases: raise ValueError("case pack is empty")
    for case in cases:
        ScenarioRequest.model_validate(case["input"])
        if schema_errors(case["input"], case["expected_output"]):
            raise ValueError(f"invalid reference schema: {case['id']}")
    return cases


def interpretation_metrics(actual: list[dict], expected: list[dict]) -> dict[str, float]:
    counts = dict.fromkeys(("relevance", "type", "hours", "numbers_shape"), 0.0)
    for i, truth in enumerate(expected):
        if i >= len(actual) or not isinstance(actual[i], dict): continue
        got = actual[i]
        if type(got.get("note_index")) is not int or got["note_index"] != i: continue
        counts["relevance"] += got.get("applies") is truth["applies"]
        same_type = got.get("directive_type") == truth["directive_type"]
        counts["type"] += same_type
        a, b = got.get("structured_adjustment"), truth["structured_adjustment"]
        if b is None:
            counts["hours"] += same_type and a is None
            counts["numbers_shape"] += same_type and a is None
        elif isinstance(a, dict):
            hs = a.get("hours")
            counts["hours"] += same_type and isinstance(hs, list) and all(type(h) is int for h in hs) and set(hs) == set(b["hours"])
            counts["numbers_shape"] += same_type and set(a) == set(b) and all(number(a[k]) and close(a[k], b[k]) for k in b if k != "hours")
    return {key: value / len(expected) for key, value in counts.items()}


def expected_constraints(request: ScenarioRequest, expected: list[dict]) -> ConstraintSet:
    """Ground truth never passes through the service's interpretation/guardrails."""
    c = ConstraintSet([h.solar_kwh for h in request.ordered_hours()],
                      [request.battery.minimum_energy_kwh] * 24, [None] * 24,
                      [False] * 24, [False] * 24)
    for entry in expected:
        kind, adj = entry["directive_type"], entry["structured_adjustment"]
        if kind == "no_op": continue
        c.applied.append(Directive(entry["note_index"], kind, adj, "case-pack ground truth"))
        for h in adj["hours"]:
            if kind == "solar_reduction": c.effective_solar_kwh[h] *= adj["factor"]
            elif kind == "minimum_battery_reserve": c.min_energy_kwh[h] = max(c.min_energy_kwh[h], adj["minimum_energy_kwh"])
            elif kind == "max_grid_window": c.max_grid_kwh[h] = min(c.max_grid_kwh[h] if c.max_grid_kwh[h] is not None else math.inf, adj["max_grid_kwh"])
            elif kind == "no_charge_window": c.charge_blocked[h] = True
            elif kind == "no_discharge_window": c.discharge_blocked[h] = True
    return c


def replay(request: ScenarioRequest, output: dict, constraints: ConstraintSet) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {k: [] for k in ("directives", "balance_solar", "battery", "actions_neutrality", "totals")}
    battery = request.battery
    energy = battery.initial_energy_kwh
    hours = request.ordered_hours()
    plan = sorted(output["hourly_plan"], key=lambda p: p["hour"])
    for row in plan:
        h = row["hour"]
        g, solar, amount, after = (row[k] for k in ("grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh"))
        charge = amount if row["battery_action"] == "charge" else 0
        discharge = amount if row["battery_action"] == "discharge" else 0
        if (constraints.charge_blocked[h] and charge > TOL or constraints.discharge_blocked[h] and discharge > TOL or after < constraints.min_energy_kwh[h] - TOL or constraints.max_grid_kwh[h] is not None and g > constraints.max_grid_kwh[h] + TOL or solar > constraints.effective_solar_kwh[h] + TOL):
            issues["directives"].append(f"hour {h}: ground-truth directive violated")
        if not close(g + solar + discharge, hours[h].demand_kwh + charge) or solar > constraints.effective_solar_kwh[h] + TOL:
            issues["balance_solar"].append(f"hour {h}: balance/effective solar")
        if not close(after, energy + charge - discharge) or after > battery.capacity_kwh + TOL or after < battery.minimum_energy_kwh - TOL or charge > battery.max_charge_kwh_per_hour + TOL or discharge > battery.max_discharge_kwh_per_hour + TOL:
            issues["battery"].append(f"hour {h}: state/bounds/rate")
        if row["battery_action"] == "idle" and not close(amount, 0):
            issues["actions_neutrality"].append(f"hour {h}: idle magnitude")
        energy = after
    if not close(energy, battery.initial_energy_kwh):
        issues["actions_neutrality"].append("end-of-day battery neutrality")
    totals = {
        "total_grid_kwh": math.fsum(p["grid_kwh"] for p in plan),
        "total_cost_bdt": math.fsum(p["grid_kwh"] * hours[p["hour"]].tariff_bdt_per_kwh for p in plan),
        "peak_grid_kwh": max(p["grid_kwh"] for p in plan),
    }
    for key, value in totals.items():
        if not close(value, output[key]): issues["totals"].append(f"{key}: reported total disagrees with replay")
    return issues


def quality_ratio(reference: float, cost: float, valid: bool) -> float:
    if not valid: return 0.0
    if reference <= TOL: return 1.0 if cost <= TOL else 0.0
    return min(1.0, reference / cost) if cost > TOL else 1.0


def evaluate_case(case: dict, output: Any) -> dict:
    result: dict = {"id": case["id"], "schema_errors": schema_errors(case["input"], output),
                    "valid": False, "quality_ratio": 0.0, "shared_verifier": "not_run",
                    "interpretation": dict.fromkeys(("relevance", "type", "hours", "numbers_shape"), 0.0)}
    if result["schema_errors"]: return result
    req = ScenarioRequest.model_validate(case["input"])
    expected = case["expected_output"]["directive_interpretation"]
    result["interpretation"] = interpretation_metrics(output["directive_interpretation"], expected)
    constraints = expected_constraints(req, expected)
    try:
        result["violations"] = replay(req, output, constraints)
    except (OverflowError, ValueError):
        result["schema_errors"].append("energy/cost arithmetic overflow")
        return result
    result["valid"] = not any(result["violations"].values())
    try:
        shared = verifier.verify([HourPlan.model_validate(p) for p in output["hourly_plan"]], req.ordered_hours(), req.battery, constraints)
        if not isinstance(shared, list) or any(not isinstance(item, str) for item in shared):
            raise TypeError("invalid verifier result")
        result["shared_verifier"] = "passed" if not shared else "rejected"
        result["shared_violation_count"] = len(shared)
        result["valid"] = result["valid"] and not shared
    except NotImplementedError:
        result["shared_verifier"] = "unimplemented"
    except Exception as exc:
        result["shared_verifier"] = f"error:{type(exc).__name__}"
        result["valid"] = False
    result["recalculated_cost_bdt"] = math.fsum(p["grid_kwh"] * req.ordered_hours()[p["hour"]].tariff_bdt_per_kwh for p in output["hourly_plan"])
    if not math.isfinite(result["recalculated_cost_bdt"]):
        result["recalculated_cost_bdt"] = None
        result["valid"] = False
        result["schema_errors"].append("cost arithmetic overflow")
        return result
    result["quality_ratio"] = quality_ratio(case["expected_output"]["total_cost_bdt"], result["recalculated_cost_bdt"], result["valid"])
    return result


async def probe(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> dict:
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(client.request(method, path, **kwargs), timeout=30)
        elapsed = time.perf_counter() - started
        try: body = strict_json(response.text)
        except (ValueError, TypeError): body = None
        return {"status": response.status_code, "body": body, "seconds": elapsed, "error": None}
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        return {"status": None, "body": None, "seconds": time.perf_counter() - started, "error": type(exc).__name__}


def health_ok(result: dict) -> bool:
    return result["status"] == 200 and isinstance(result["body"], dict) and result["body"].get("status") == "ok"


async def run_judge(client: httpx.AsyncClient, cases: list[dict], repeat: int = 1) -> dict:
    if not cases or repeat < 1: raise ValueError("nonempty cases and positive repeat required")
    health = await probe(client, "GET", "/health")
    malformed = await probe(client, "POST", "/optimize-energy", content='{"scenario_id":', headers={"Content-Type": "application/json"})
    structural = await probe(client, "POST", "/optimize-energy", json={"scenario_id": "invalid"})
    after = await probe(client, "GET", "/health")
    results = []
    for iteration in range(repeat):
        for case in cases:
            response = await probe(client, "POST", "/optimize-energy", json=case["input"])
            good_http = response["status"] == 200 and response["body"] is not None and response["seconds"] <= 30
            result = evaluate_case(case, response["body"]) if good_http else {
                "id": case["id"], "valid": False, "quality_ratio": 0.0,
                "schema_errors": ["HTTP/JSON/timeout failure"], "interpretation": {}, "shared_verifier": "not_run"}
            result.update(repeat=iteration + 1, status=response["status"], seconds=response["seconds"], transport_error=response["error"], http_ok=good_http)
            results.append(result)
    latency = sorted(r["seconds"] for r in results)
    p95 = latency[math.ceil(0.95 * len(latency)) - 1]
    fraction = lambda test: sum(bool(test(r)) for r in results) / len(results)
    interpretation = sum(sum(r["interpretation"].values()) * 5 for r in results) / len(results)
    weights = {"directives": 10, "balance_solar": 5, "battery": 5, "actions_neutrality": 5}
    application = sum(sum(w for k, w in weights.items() if not r["violations"][k] and (k != "directives" or r["valid"])) if "violations" in r else 0 for r in results) / len(results)
    bad_ok = malformed["status"] == 400 and isinstance(malformed["body"], dict) and health_ok(after)
    categories = [
        {"name": "LLM Directive Interpretation", "points": interpretation, "maximum": 25, "unassessed": "5 paraphrase points; real LLM use requires source/runtime evidence"},
        {"name": "Directive Application & Constraint Correctness", "points": application, "maximum": 25},
        {"name": "Optimization Quality", "points": 10 * statistics.mean(r["quality_ratio"] for r in results), "maximum": 10},
        {"name": "API Contract & Schema", "points": float(health_ok(health)) + fraction(lambda r: r["http_ok"]) + 2 * (structural["status"] == 400) + 6 * fraction(lambda r: not r["schema_errors"]), "maximum": 10},
        {"name": "Performance & Reliability", "points": float(health_ok(health)) + (3 if p95 <= 5 else 2 if p95 <= 15 else 1 if p95 <= 30 else 0) * fraction(lambda r: r["http_ok"]) + 3 * fraction(lambda r: r["http_ok"]) + float(bad_ok), "maximum": 10, "unassessed": "startup readiness within 60s; provider failure and secret safety"},
        {"name": "Deployment & Docker Fallback", "points": None, "maximum": 10, "unassessed": "external reachability, clean pull/start and immutable image"},
        {"name": "Documentation & Local Reproducibility", "points": None, "maximum": 10, "unassessed": "fresh-machine walkthrough and submission artifacts"},
    ]
    return {"score_kind": "local diagnostic estimate; not official score", "categories": categories,
            "measured_points_out_of_100": sum(c["points"] or 0 for c in categories),
            "cases": results, "p95_seconds": p95, "failure_rate": 1 - fraction(lambda r: r["http_ok"]),
            "invalid_plan_rate": 1 - fraction(lambda r: r["valid"]),
            "valid_cases": sum(r["valid"] for r in results), "requests": len(results),
            "health_ok": health_ok(health), "malformed_ok": bad_ok, "structural_ok": structural["status"] == 400,
            "ready": health_ok(health) and bad_ok and structural["status"] == 400 and all(r["valid"] and all(v == 1 for v in r["interpretation"].values()) and r["shared_verifier"] == "passed" for r in results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/data/public_samples.json")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, help="write JSON report")
    args = parser.parse_args()
    if args.repeat < 1: parser.error("--repeat must be positive")
    url = httpx.URL(args.base_url)
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        parser.error("base URL must be HTTP(S), without embedded credentials")
    try: cases = load_cases(args.cases)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f"cannot load case pack ({type(exc).__name__})")

    async def run() -> dict:
        async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=30, follow_redirects=False) as client:
            return await run_judge(client, cases, args.repeat)
    report = asyncio.run(run())
    encoded = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    if args.json: print(encoded)
    else:
        print(report["score_kind"])
        for category in report["categories"]:
            points = "UNASSESSED" if category["points"] is None else f"{category['points']:.2f}"
            print(f"{category['name']:<46} {points:>10} / {category['maximum']}")
            if category.get("unassessed"): print("  Pending: " + category["unassessed"])
        print(f"Measured: {report['measured_points_out_of_100']:.2f}/100; valid {report['valid_cases']}/{report['requests']}; p95 {report['p95_seconds']:.3f}s; failures {report['failure_rate']:.1%}")
        for result in report["cases"]:
            print(f"{result['id']} repeat={result['repeat']} HTTP={result['status']} valid={result['valid']} shared={result['shared_verifier']}")
        print("Integration checks: " + ("PASS (artifact review still required)" if report["ready"] else "FAIL"))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
