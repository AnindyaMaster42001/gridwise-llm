"""
Lane B self-check.  OWNER: Ninad, Lane B.

`harness/judge.py` (Fayek) scores the whole service against a running URL. This
runs one lane offline, so a prompt edit can be checked in seconds without a
deployment. The specific failure this guards against is a prompt change that
fixes one wording and quietly breaks three others.

    python3 -m app.llm.bench bank        # is the bank's own ground truth legal?
    python3 -m app.llm.bench fallback    # regex interpreter vs the bank
    python3 -m app.llm.bench samples     # the 10 public cases, offline
    python3 -m app.llm.bench selftest    # client/cache/failover, mocked HTTP
    python3 -m app.llm.bench llm         # the real model vs the bank (needs a key)

Lives under app/llm/ rather than tests/ on purpose: `tests/*` is Fayek's lane.
When the lanes merge, `selftest` is the thing to lift into tests/test_llm.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from app.schemas import BatteryInput

ROOT = pathlib.Path(__file__).resolve().parents[2]
BANK = ROOT / "harness" / "paraphrase_bank.json"
SAMPLES = ROOT / "tests" / "data" / "public_samples.json"
TOL = 0.01
NUMERIC_KEYS = ("factor", "minimum_energy_kwh", "max_grid_kwh")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def compare(got: Dict[str, Any], expected: Dict[str, Any]) -> Optional[str]:
    """None when the raw interpretation matches ground truth, else the field."""
    if got.get("directive_type") != expected["directive_type"]:
        return "directive_type"
    if expected["directive_type"] == "no_op":
        return None
    if list(got.get("hours") or []) != list(expected.get("hours") or []):
        return "hours"
    for key in NUMERIC_KEYS:
        if key not in expected or expected[key] is None:
            continue
        value = got.get(key)
        if value is None:
            return key
        try:
            if abs(float(value) - float(expected[key])) > TOL:
                return key
        except (TypeError, ValueError):
            return key
    return None


def report(rows: List[Tuple[str, Dict[str, Any], Dict[str, Any], Optional[str]]]) -> int:
    """Print a per-type table and every miss. Returns the process exit code."""
    per_type: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    for _, _, expected, miss in rows:
        bucket = per_type[expected["directive_type"]]
        bucket[1] += 1
        if miss is None:
            bucket[0] += 1

    print(f"\n{'directive_type':<26}{'correct':>10}{'total':>8}{'accuracy':>11}")
    print("-" * 55)
    total_ok = total = 0
    for name in sorted(per_type):
        ok, count = per_type[name]
        total_ok += ok
        total += count
        print(f"{name:<26}{ok:>10}{count:>8}{ok / count:>10.0%}")
    print("-" * 55)
    print(f"{'ALL':<26}{total_ok:>10}{total:>8}{(total_ok / total if total else 0):>10.0%}\n")

    misses = [row for row in rows if row[3] is not None]
    if misses:
        print(f"{len(misses)} miss(es):")
        for note, got, expected, field in misses:
            print(f"\n  note      {note}")
            print(f"  expected  {expected['directive_type']} {_brief(expected)}")
            print(f"  got       {got.get('directive_type')} {_brief(got)}")
            print(f"  wrong     {field}")
        print()
    return 0 if not misses else 1


def _brief(entry: Dict[str, Any]) -> str:
    parts = []
    hours = entry.get("hours")
    if hours:
        parts.append(f"hours={_compact(hours)}")
    for key in NUMERIC_KEYS:
        if entry.get(key) is not None:
            parts.append(f"{key}={entry[key]}")
    return " ".join(parts) or "-"


def _compact(hours: List[int]) -> str:
    if len(hours) > 8:
        return f"[{hours[0]}..{hours[-1]}] ({len(hours)}h)"
    return str(hours)


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------


def load_bank() -> Tuple[BatteryInput, List[Dict[str, Any]]]:
    data = json.loads(BANK.read_text())
    return BatteryInput(**data["battery_context"]), data["cases"]


def load_samples() -> List[Dict[str, Any]]:
    return json.loads(SAMPLES.read_text())["cases"]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_bank(_: argparse.Namespace) -> int:
    """Check the bank's own ground truth against the Problem Statement rules.

    A test set with an illegal expectation in it is worse than no test set: it
    teaches the prompt to be wrong.
    """
    battery, cases = load_bank()
    problems: List[str] = []
    for index, case in enumerate(cases):
        expected = case["expected"]
        label = f"case {index} ({case['note'][:48]}...)"
        directive_type = expected.get("directive_type")
        if directive_type not in {
            "solar_reduction", "minimum_battery_reserve", "no_charge_window",
            "no_discharge_window", "max_grid_window", "no_op",
        }:
            problems.append(f"{label}: unknown directive_type {directive_type!r}")
            continue
        hours = expected.get("hours")
        if directive_type == "no_op":
            if hours:
                problems.append(f"{label}: no_op must not carry hours")
            continue
        if not isinstance(hours, list) or not hours:
            problems.append(f"{label}: missing hours")
            continue
        if hours != sorted(set(hours)):
            problems.append(f"{label}: hours not unique and ascending: {hours}")
        if any(not isinstance(h, int) or not 0 <= h <= 23 for h in hours):
            problems.append(f"{label}: hours outside 0..23: {hours}")
        if directive_type == "solar_reduction":
            factor = expected.get("factor")
            if factor is None or not 0.0 <= float(factor) <= 1.0:
                problems.append(f"{label}: factor must be in [0, 1], got {factor}")
        if directive_type == "minimum_battery_reserve":
            reserve = expected.get("minimum_energy_kwh")
            if reserve is None or not 0.0 <= float(reserve) <= battery.capacity_kwh:
                problems.append(f"{label}: reserve must be 0..capacity, got {reserve}")
        if directive_type == "max_grid_window":
            cap = expected.get("max_grid_kwh")
            if cap is None or float(cap) < 0:
                problems.append(f"{label}: max_grid_kwh must be finite and >= 0")

    counts: Dict[str, int] = defaultdict(int)
    for case in cases:
        counts[case["expected"]["directive_type"]] += 1
    print(f"{len(cases)} cases, capacity {battery.capacity_kwh:g} kWh")
    for name in sorted(counts):
        flag = "" if counts[name] >= 12 or name == "no_op" else "   << under 12"
        print(f"  {name:<26}{counts[name]:>4}{flag}")
    if problems:
        print(f"\n{len(problems)} problem(s) in the bank's own ground truth:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nevery expectation obeys the Problem Statement rules")
    return 0


def cmd_fallback(_: argparse.Namespace) -> int:
    from app.llm.fallback import rule_based_interpret

    battery, cases = load_bank()
    rows = []
    started = time.perf_counter()
    for case in cases:
        got = rule_based_interpret([case["note"]], battery)[0]
        rows.append((case["note"], got, case["expected"], compare(got, case["expected"])))
    elapsed = (time.perf_counter() - started) * 1000
    print(f"regex fallback over {len(cases)} paraphrases in {elapsed:.0f} ms")
    return report(rows)


def cmd_samples(_: argparse.Namespace) -> int:
    """The 10 public cases, through the regex path (no key needed)."""
    from app.llm.fallback import rule_based_interpret

    rows = []
    for case in load_samples():
        battery = BatteryInput(**case["input"]["battery"])
        notes = case["input"]["operator_notes"]
        got = rule_based_interpret(notes, battery)
        for entry, truth in zip(got, case["expected_output"]["directive_interpretation"]):
            expected = {"directive_type": truth["directive_type"]}
            expected.update(truth["structured_adjustment"] or {})
            rows.append((notes[entry["note_index"]], entry, expected, compare(entry, expected)))
    print(f"public sample pack, offline path, {len(rows)} notes")
    return report(rows)


def cmd_llm(args: argparse.Namespace) -> int:
    """The real model against the bank. Costs money; needs LLM_API_KEY."""
    from app.llm.client import build_fallback, build_primary
    from app.llm.interpreter import cache_stats, clear_cache, interpret_notes

    if build_primary() is None and build_fallback() is None:
        print("no provider configured: set LLM_PROVIDER / LLM_MODEL / LLM_API_KEY", file=sys.stderr)
        return 2

    battery, cases = load_bank()
    if args.limit:
        cases = cases[: args.limit]
    batches = [cases[i : i + args.batch] for i in range(0, len(cases), args.batch)]

    async def run() -> List[Tuple[str, Dict[str, Any], Dict[str, Any], Optional[str]]]:
        from app.llm.interpreter import aclose

        clear_cache()
        rows = []
        latencies: List[float] = []
        semaphore = asyncio.Semaphore(args.concurrency)

        async def one(batch: List[Dict[str, Any]]):
            notes = [case["note"] for case in batch]
            async with semaphore:
                started = time.perf_counter()
                entries = await interpret_notes(notes, battery)
                latencies.append((time.perf_counter() - started) * 1000)
            return batch, entries

        results = await asyncio.gather(
            *(one(batch) for batch in batches), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                print(f"  batch failed: {result}", file=sys.stderr)
                continue
            batch, entries = result
            for case, entry in zip(batch, entries):
                rows.append(
                    (case["note"], entry, case["expected"], compare(entry, case["expected"]))
                )
        if latencies:
            latencies.sort()
            p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
            print(
                f"{len(batches)} call(s), {args.batch} note(s) each: "
                f"median {latencies[len(latencies) // 2]:.0f} ms, p95 {p95:.0f} ms"
            )
        await aclose()
        return rows

    rows = asyncio.run(run())
    exit_code = report(rows)
    print(f"cache: {cache_stats()}")
    return exit_code


def cmd_selftest(_: argparse.Namespace) -> int:
    """Client, cache, failover and extraction, against a mocked transport.

    No key and no network: this is the part of Lane B that can be checked on a
    laptop with the wifi off, and it is what should move into tests/ at merge.
    """
    import httpx

    from app.llm import client as client_module
    from app.llm import interpreter as interpreter_module
    from app.llm.client import LLMClient, LLMError
    from app.llm.interpreter import (
        InterpretationUnavailable,
        clear_cache,
        extract_json,
        interpret_notes,
    )

    battery = BatteryInput(
        capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=40,
        max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50,
    )
    secret = "fake-key-for-the-mocked-transport-only"
    failures: List[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"  {'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  ' + detail}")
        if not condition:
            failures.append(name)

    def answer(payload: Dict[str, Any], provider: str = "openai") -> httpx.Response:
        text = json.dumps(payload)
        if provider == "gemini":
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    one_entry = {
        "interpretations": [
            {
                "note_index": 0, "directive_type": "no_charge_window",
                "hours": [2, 3, 4], "factor": None, "minimum_energy_kwh": None,
                "max_grid_kwh": None, "explanation": "ok",
            }
        ]
    }

    print("\nprovider request shapes")
    seen: Dict[str, httpx.Request] = {}

    def record(request: httpx.Request) -> httpx.Response:
        seen[str(request.url)] = request
        return answer(one_entry, "gemini" if "generativelanguage" in str(request.url) else "openai")

    transport = httpx.MockTransport(record)

    async def shapes() -> None:
        openai = LLMClient("openai", "gpt-4o-mini", secret, timeout_s=5, transport=transport)
        await openai.complete_json("sys", "user", schema={"type": "object"}, few_shot=[{"user": "u", "assistant": "a"}])
        request = seen["https://api.openai.com/v1/chat/completions"]
        body = json.loads(request.content)
        check("openai hits /v1/chat/completions", True)
        check("openai sends a bearer header", request.headers.get("authorization") == f"Bearer {secret}")
        check("openai asks for a json_schema", body["response_format"]["type"] == "json_schema")
        check("openai sends strict mode", body["response_format"]["json_schema"]["strict"] is True)
        check("few-shot becomes user/assistant turns", len(body["messages"]) == 4, str(len(body["messages"])))
        check("temperature is 0", body["temperature"] == 0.0)
        await openai.aclose()

        groq = LLMClient("groq", "llama-3.3-70b-versatile", secret, timeout_s=5, transport=transport)
        await groq.complete_json("sys", "user", schema={"type": "object"})
        body = json.loads(seen["https://api.groq.com/openai/v1/chat/completions"].content)
        check("groq falls back to json_object", body["response_format"] == {"type": "json_object"})
        await groq.aclose()

        gemini = LLMClient("gemini", "gemini-2.0-flash", secret, timeout_s=5, transport=transport)
        result = await gemini.complete_json("sys", "user", schema={"type": "object", "properties": {}})
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
        request = seen[url]
        body = json.loads(request.content)
        check("gemini key travels in a header, not the URL", secret not in str(request.url))
        check("gemini uses x-goog-api-key", request.headers.get("x-goog-api-key") == secret)
        check("gemini asks for JSON output", body["generationConfig"]["responseMimeType"] == "application/json")
        check("gemini gets a responseSchema", "responseSchema" in body["generationConfig"])
        check("gemini response is parsed", json.loads(result.text) == one_entry)
        await gemini.aclose()

    asyncio.run(shapes())

    print("\nerrors never carry the key")

    async def redaction() -> None:
        def unauthorised(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text=f'{{"error":"invalid key {secret} supplied"}}')

        bad = LLMClient("openai", "m", secret, timeout_s=5, transport=httpx.MockTransport(unauthorised))
        try:
            await bad.complete_json("s", "u")
            check("401 raises LLMError", False)
        except LLMError as exc:
            check("401 raises LLMError", True)
            check("error text has no key", secret not in str(exc), str(exc))
            check("error text keeps the status code", "401" in str(exc))
        await bad.aclose()

        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        stalled = LLMClient("openai", "m", secret, timeout_s=2, transport=httpx.MockTransport(slow))
        try:
            await stalled.complete_json("s", "u")
            check("timeout raises LLMError", False)
        except LLMError as exc:
            check("timeout raises LLMError", True)
            check("timeout text has no key", secret not in str(exc))
        await stalled.aclose()

    asyncio.run(redaction())

    print("\ntolerant JSON extraction")
    check("strips ```json fences", extract_json('```json\n{"a":1}\n```') == {"a": 1})
    check("survives leading prose", extract_json('Sure! Here it is:\n{"a":1}') == {"a": 1})
    check("survives trailing commentary", extract_json('{"a":1}\nHope that helps.') == {"a": 1})
    check("wraps a bare array", extract_json('[{"note_index":0}]') == {"interpretations": [{"note_index": 0}]})
    check("tolerates a trailing comma", extract_json('{"a":[1,2,],}') == {"a": [1, 2]})
    check(
        "a brace inside a string does not end the object",
        extract_json('{"explanation":"use {curly} braces","a":1}')["a"] == 1,
    )
    try:
        extract_json("I could not do that.")
        check("unparseable text raises ValueError", False)
    except ValueError:
        check("unparseable text raises ValueError", True)

    print("\ninterpreter: failover, cache, alignment")

    calls = {"primary": 0, "fallback": 0}

    def make(provider_name: str, handler) -> LLMClient:
        return LLMClient(provider_name, "m", secret, timeout_s=5, transport=httpx.MockTransport(handler))

    def dead(request: httpx.Request) -> httpx.Response:
        calls["primary"] += 1
        return httpx.Response(503, text="upstream unavailable")

    def alive(request: httpx.Request) -> httpx.Response:
        calls["fallback"] += 1
        body = json.loads(request.content)
        note_count = body["messages"][-1]["content"].count("\n[")
        entries = [
            {
                "note_index": index, "directive_type": "no_op", "hours": None,
                "factor": None, "minimum_energy_kwh": None, "max_grid_kwh": None,
                "explanation": "distractor",
            }
            for index in range(max(1, note_count))
        ]
        return answer({"interpretations": entries})

    async def failover() -> None:
        client_module.reset_clients()
        client_module._primary = make("openai", dead)
        client_module._fallback = make("groq", alive)
        client_module._built = True
        clear_cache()

        entries = await interpret_notes(["The menu changes tomorrow."], battery)
        check("a dead primary fails over to the second provider", entries[0]["directive_type"] == "no_op")
        check("the primary was retried before failover", calls["primary"] == 2, str(calls["primary"]))
        check("entries are stamped with source", entries[0].get("source") == "llm")

        before = calls["fallback"]
        await interpret_notes(["The menu changes tomorrow."], battery)
        check("an identical second request is a cache hit", calls["fallback"] == before)

        await interpret_notes(["the MENU  changes tomorrow"], battery)
        check("cache key ignores case and punctuation", calls["fallback"] == before)

    asyncio.run(failover())

    revoked = {"primary": 0, "fallback": 0}

    def unauthorised_primary(request: httpx.Request) -> httpx.Response:
        revoked["primary"] += 1
        return httpx.Response(401, text='{"error":{"message":"Incorrect API key provided"}}')

    def healthy_secondary(request: httpx.Request) -> httpx.Response:
        revoked["fallback"] += 1
        return answer(
            {
                "interpretations": [
                    {
                        "note_index": 0, "directive_type": "no_charge_window",
                        "hours": [2, 3, 4], "factor": None, "minimum_energy_kwh": None,
                        "max_grid_kwh": None, "explanation": "ok",
                    }
                ]
            }
        )

    async def revoked_key() -> None:
        client_module.reset_clients()
        client_module._primary = make("openai", unauthorised_primary)
        client_module._fallback = make("groq", healthy_secondary)
        client_module._built = True
        clear_cache()

        entries = await interpret_notes(["no charging from 2 AM to 5 AM"], battery)
        check(
            "a revoked key still yields an interpretation via failover",
            entries[0]["directive_type"] == "no_charge_window",
        )
        check(
            "a revoked key is NOT retried (401 is permanent)",
            revoked["primary"] == 1,
            f"called the dead provider {revoked['primary']} times",
        )
        check("the healthy vendor answered once", revoked["fallback"] == 1)

    asyncio.run(revoked_key())

    def scrambled(request: httpx.Request) -> httpx.Response:
        return answer(
            {
                "interpretations": [
                    {"note_index": 2, "directive_type": "no_op", "explanation": "third"},
                    {"note_index": 0, "directive_type": "solar_reduction", "hours": [13, 14], "factor": 0.2, "explanation": "first"},
                ]
            }
        )

    async def alignment() -> None:
        client_module.reset_clients()
        client_module._primary = make("openai", scrambled)
        client_module._fallback = None
        client_module._built = True
        clear_cache()

        entries = await interpret_notes(["a solar note", "a second note", "a third note"], battery)
        check("always one entry per note", len(entries) == 3, str(len(entries)))
        check("note_index is positional 0..N-1", [e["note_index"] for e in entries] == [0, 1, 2])
        check("out-of-order entries land on the right note", entries[0]["directive_type"] == "solar_reduction")
        check("a note the model skipped is left blank, not invented", entries[1]["directive_type"] is None)
        check("the model's third entry stays third", entries[2]["explanation"] == "third")

    asyncio.run(alignment())

    async def exhausted() -> None:
        client_module.reset_clients()
        client_module._primary = make("openai", dead)
        client_module._fallback = None
        client_module._built = True
        clear_cache()
        try:
            await interpret_notes(["anything"], battery)
            check("every provider down raises InterpretationUnavailable", False)
        except InterpretationUnavailable as exc:
            check("every provider down raises InterpretationUnavailable", True)
            check("that error carries no key either", secret not in str(exc))

    asyncio.run(exhausted())
    client_module.reset_clients()
    interpreter_module.clear_cache()

    print()
    if failures:
        print(f"{len(failures)} self-test failure(s): {', '.join(failures)}\n")
        return 1
    print("all self-tests passed\n")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m app.llm.bench", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bank", help="validate the bank's own ground truth").set_defaults(func=cmd_bank)
    sub.add_parser("fallback", help="regex interpreter vs the bank").set_defaults(func=cmd_fallback)
    sub.add_parser("samples", help="public sample pack, offline").set_defaults(func=cmd_samples)
    sub.add_parser("selftest", help="client/cache/failover with mocked HTTP").set_defaults(func=cmd_selftest)
    live = sub.add_parser("llm", help="the configured model vs the bank")
    live.add_argument("--batch", type=int, default=3, help="notes per call (the judge sends 1-3)")
    live.add_argument("--concurrency", type=int, default=4)
    live.add_argument("--limit", type=int, default=0, help="only the first N cases")
    live.set_defaults(func=cmd_llm)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
