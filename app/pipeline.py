"""
Orchestration: notes -> LLM -> guardrails -> optimizer -> verifier -> response.
OWNER: Anindya — Lane A.

`run_pipeline()` is the only function main.py calls, and it carries one promise:
**a well-formed scenario always gets a valid 200 inside the time budget**, no
matter which downstream module is late, slow, or wrong. Everything below exists
to keep that promise.

Integration shims
-----------------
Ninad (`app/llm/*`) and Kabya (`app/guardrails.py`, `app/optimizer.py`,
`app/verifier.py`) are building in parallel, so their functions still raise
`NotImplementedError`. Every call into their modules goes through a `_call_*`
shim that catches exactly that and drops to a local `_stub_*`. The moment their
real implementation lands on `main`, the shim starts using it with no edit here.

Each shim logs at WARNING when a stub fires, so a stub that survives integration
is loud rather than silent. Search for `STUB` to find them all.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.config import get_settings
from app.schemas import (
    DIRECTIVE_TYPES,
    HORIZON,
    TOL,
    BatteryInput,
    ConstraintSet,
    Directive,
    HourInput,
    HourPlan,
    OptimizeResponse,
    ScenarioRequest,
)

log = logging.getLogger("gridwise.pipeline")
settings = get_settings()

# Values smaller than this are numerical dust from a solver, not energy.
_SNAP = 1e-9
# Emitted numbers are rounded here so that the totals we report and the totals
# the judge recalculates from hourly_plan are computed from identical inputs.
_ROUND = 6
# Seconds reserved after interpretation for solving, verifying and serialising.
_TAIL_RESERVE_S = 3.0
# Below this much remaining budget, skip the model rather than start a call that
# cannot finish. Ninad's LLM_TIMEOUT_S is the primary control; this is a backstop.
_MIN_MODEL_BUDGET_S = 3.0
# A solve always gets at least this long, even past the deadline: the LP takes
# milliseconds, and abandoning it would cost a whole case for nothing.
_MIN_SOLVE_BUDGET_S = 0.25


# ==========================================================================
# Entry point
# ==========================================================================


async def run_pipeline(payload: ScenarioRequest, request_id: str = "") -> OptimizeResponse:
    """Full request path. Must not raise for any well-formed scenario."""
    deadline = time.perf_counter() + settings.request_budget_s
    hours = payload.ordered_hours()
    battery = payload.battery

    try:
        # --- 1. mandatory LLM step -------------------------------------
        raw, source = await _call_interpreter(
            payload.operator_notes, battery, _remaining(deadline) - _TAIL_RESERVE_S, request_id
        )

        # --- 2. untrusted -> trusted -----------------------------------
        directives = _call_guardrails(raw, payload.operator_notes, battery, source)

        # --- 3. solve, degrading rather than failing --------------------
        plan, rung = await _solve_with_ladder(hours, battery, directives, deadline, request_id)

        # --- 4. totals recomputed from the emitted plan -----------------
        total_grid, total_cost, peak = summarise_totals(
            plan, [h.tariff_bdt_per_kwh for h in hours]
        )

        log.info(
            "request_id=%s scenario=%s source=%s rung=%s applied=%d cost=%.2f",
            request_id,
            payload.scenario_id,
            source,
            rung,
            sum(1 for d in directives if d.applies),
            total_cost,
        )

        return OptimizeResponse(
            scenario_id=payload.scenario_id,
            directive_interpretation=[d.to_interpretation() for d in directives],
            hourly_plan=plan,
            total_grid_kwh=total_grid,
            total_cost_bdt=total_cost,
            peak_grid_kwh=peak,
            plan_summary=build_plan_summary(
                directives, plan, total_grid, total_cost, peak, source, rung
            ),
        )

    except Exception:  # pragma: no cover — the last line before a 500
        log.exception("request_id=%s pipeline failed; returning emergency plan", request_id)
        return _emergency_response(payload, hours, battery)


# ==========================================================================
# Totals and summary
# ==========================================================================


def summarise_totals(plan: List[HourPlan], tariffs: Sequence[float]) -> Tuple[float, float, float]:
    """Return (total_grid_kwh, total_cost_bdt, peak_grid_kwh) recomputed from `plan`.

    `tariffs` is indexed by hour. The judge recalculates these three numbers from
    hourly_plan and compares within 0.01, so they are derived here and nowhere
    else — never from the solver's objective value.
    """
    total_grid = 0.0
    total_cost = 0.0
    peak = 0.0
    for entry in plan:
        grid = float(entry.grid_kwh)
        total_grid += grid
        total_cost += grid * float(tariffs[entry.hour])
        peak = max(peak, grid)
    return round(total_grid, _ROUND), round(total_cost, _ROUND), round(peak, _ROUND)


def build_plan_summary(
    directives: List[Directive],
    plan: List[HourPlan],
    total_grid: float,
    total_cost: float,
    peak: float,
    source: str = "llm",
    rung: str = "all",
) -> str:
    """Short human-readable strategy sentence for `plan_summary`.

    Not machine-graded, but judges read it when resolving ties, so it must be
    truthful about what was actually applied and about any degraded path taken.
    """
    applied = [d for d in directives if d.applies]
    n_noop = len(directives) - len(applied)

    parts: List[str] = [
        f"Interpreted {len(directives)} operator note{'s' if len(directives) != 1 else ''}: "
        f"{len(applied)} applied, {n_noop} not schedule-relevant."
    ]

    if applied:
        parts.append("Applied " + "; ".join(_describe(d) for d in applied) + ".")

    charged = sum(e.battery_kwh for e in plan if e.battery_action == "charge")
    discharged = sum(e.battery_kwh for e in plan if e.battery_action == "discharge")
    if charged > TOL or discharged > TOL:
        parts.append(
            f"Shifted {charged:.1f} kWh into the battery and {discharged:.1f} kWh back out, "
            "returning to the starting state of charge by hour 23."
        )
    else:
        parts.append("Held the battery idle; shifting energy did not reduce cost here.")

    parts.append(
        f"Bought {total_grid:.2f} kWh from the grid for {total_cost:.2f} BDT, "
        f"peaking at {peak:.2f} kWh."
    )

    if source == "fallback":
        parts.append(
            "Notes were read by the deterministic backup interpreter because no "
            "language-model provider was reachable."
        )
    elif source == "stub":
        parts.append("Note interpretation is not yet wired up in this build.")

    if rung.startswith("relaxed"):
        parts.append(
            "Some extracted directives could not all hold at once, so the least "
            "confident ones were relaxed to keep the schedule feasible."
        )
    elif rung == "baseline":
        parts.append(
            "Returned a conservative grid-first schedule because no verified "
            "optimised plan was available."
        )

    return " ".join(parts)


def _describe(d: Directive) -> str:
    adj = d.adjustment or {}
    window = _format_hours(adj.get("hours", []))
    if d.directive_type == "solar_reduction":
        return f"usable solar cut to {float(adj.get('factor', 0)) * 100:.0f}% in hours {window}"
    if d.directive_type == "minimum_battery_reserve":
        return f"a {float(adj.get('minimum_energy_kwh', 0)):.0f} kWh battery reserve in hours {window}"
    if d.directive_type == "no_charge_window":
        return f"no battery charging in hours {window}"
    if d.directive_type == "no_discharge_window":
        return f"no battery discharging in hours {window}"
    if d.directive_type == "max_grid_window":
        return f"grid import capped at {float(adj.get('max_grid_kwh', 0)):.0f} kWh in hours {window}"
    return d.directive_type


def _format_hours(hours: Iterable[int]) -> str:
    """[18,19,20,21] -> '18-21'; [0,1,22,23] -> '0-1, 22-23'."""
    ordered = sorted(set(int(h) for h in hours))
    if not ordered:
        return "-"
    runs: List[List[int]] = [[ordered[0], ordered[0]]]
    for h in ordered[1:]:
        if h == runs[-1][1] + 1:
            runs[-1][1] = h
        else:
            runs.append([h, h])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


# ==========================================================================
# Step 1 — interpretation
# ==========================================================================


async def _call_interpreter(
    notes: List[str], battery: BatteryInput, budget_s: float, request_id: str
) -> Tuple[List[Dict[str, Any]], str]:
    """Run the mandatory LLM step; return (raw dicts, source label).

    `source` is one of "llm", "fallback" (regex backup) or "stub", and it is
    reported honestly in plan_summary rather than hidden.
    """
    from app.llm.fallback import rule_based_interpret
    from app.llm.interpreter import InterpretationUnavailable, interpret_notes

    # Below this there is no point starting a call we cannot finish; go straight
    # to the deterministic backup rather than burning the tail of the budget.
    if budget_s < _MIN_MODEL_BUDGET_S:
        log.warning("request_id=%s only %.1fs left; skipping the model", request_id, budget_s)
    else:
        try:
            raw = await asyncio.wait_for(interpret_notes(notes, battery), timeout=budget_s)
            return _second_opinion(list(raw or []), notes, battery, request_id), "llm"
        except NotImplementedError:
            log.warning("STUB: app.llm.interpreter.interpret_notes is not implemented yet")
            return _stub_interpret(notes), "stub"
        except asyncio.TimeoutError:
            log.warning("request_id=%s model call exceeded %.1fs; using backup", request_id, budget_s)
        except InterpretationUnavailable:
            log.warning("request_id=%s every model provider failed; using backup", request_id)
        except Exception as exc:
            # Type only, never the message and never a traceback: a provider's
            # exception text can carry a URL token or an echoed auth header, and
            # the Guide forbids secrets in logs as firmly as in responses.
            log.warning(
                "request_id=%s interpreter raised %s; using backup",
                request_id,
                type(exc).__name__,
            )

    try:
        return list(rule_based_interpret(notes, battery) or []), "fallback"
    except NotImplementedError:
        log.warning("STUB: app.llm.fallback.rule_based_interpret is not implemented yet")
    except Exception as exc:
        log.warning(
            "request_id=%s backup interpreter raised %s; all notes become no_op",
            request_id,
            type(exc).__name__,
        )
    return _stub_interpret(notes), "stub"



def _second_opinion(
    raw: List[Dict[str, Any]],
    notes: List[str],
    battery: BatteryInput,
    request_id: str,
) -> List[Dict[str, Any]]:
    """Let the deterministic interpreter cover notes the model read as no_op.

    The models in use do not fail loudly. They return a well-formed answer that
    simply does not cover every note, and the uncovered ones arrive here as
    no_op — observed live on SAMPLE-01, where a plain "treat solar as 25% of
    forecast" note came back irrelevant, and on SAMPLE-08, where both windows
    did. A missed directive is the most expensive error in this challenge: the
    judge replays the plan against its OWN directive, so the case loses its
    interpretation credit, its application credit and its optimization credit
    together.

    The regex interpreter is a poor generaliser but a precise matcher — it
    scored 12/12 on the distractors in the paraphrase bank, so it stays silent
    unless a note explicitly says something. That makes it a safe second
    opinion, and only in one direction: it may fill a no_op the model left, and
    may never overrule a directive the model actually produced.
    """
    from app.llm.fallback import rule_based_interpret

    if not notes:
        return raw

    def indexed(entries: Any) -> Dict[int, Dict[str, Any]]:
        out: Dict[int, Dict[str, Any]] = {}
        for position, entry in enumerate(entries or []):
            if not isinstance(entry, dict):
                continue
            try:
                index = int(entry.get("note_index", position))
            except (TypeError, ValueError):
                index = position
            out.setdefault(index, entry)
        return out

    model = indexed(raw)
    gaps = [
        i
        for i in range(len(notes))
        if str((model.get(i) or {}).get("directive_type", "no_op")).strip().lower() == "no_op"
    ]
    if not gaps:
        return raw

    try:
        backup = indexed(rule_based_interpret(notes, battery))
    except Exception as exc:
        log.warning("request_id=%s second opinion unavailable (%s)", request_id, type(exc).__name__)
        return raw

    filled: List[int] = []
    for i in gaps:
        candidate = backup.get(i)
        if not candidate:
            continue
        kind = str(candidate.get("directive_type", "no_op")).strip().lower()
        if kind == "no_op" or kind not in DIRECTIVE_TYPES:
            continue
        model[i] = dict(candidate, note_index=i)
        filled.append(i)

    if filled:
        log.warning(
            "request_id=%s model returned no_op for note(s) %s; the deterministic "
            "interpreter found a directive there and was used instead",
            request_id,
            filled,
        )
    return [model.get(i, {"note_index": i, "directive_type": "no_op"}) for i in range(len(notes))]


def _stub_interpret(notes: List[str]) -> List[Dict[str, Any]]:
    """STUB — remove at integration. One no_op per note, never a guess."""
    return [{"note_index": i, "directive_type": "no_op"} for i in range(len(notes))]


# ==========================================================================
# Step 2 — guardrails
# ==========================================================================


def _call_guardrails(
    raw: List[Dict[str, Any]], notes: List[str], battery: BatteryInput, source: str
) -> List[Directive]:
    from app.guardrails import validate_interpretations

    try:
        directives = list(validate_interpretations(raw, notes, battery))
    except NotImplementedError:
        log.warning("STUB: app.guardrails.validate_interpretations is not implemented yet")
        directives = _stub_validate(raw, notes, battery)
    except Exception:
        log.exception("guardrails raised; every note falls back to no_op")
        directives = _all_no_op(notes, "Interpretation could not be validated.")

    if source in {"fallback", "stub"}:
        for d in directives:
            d.source = source

    return _final_schema_guard(directives, notes, battery)


def _all_no_op(notes: List[str], explanation: str) -> List[Directive]:
    return [
        Directive(
            note_index=i,
            directive_type="no_op",
            adjustment=None,
            explanation=explanation,
            source="safe_default",
        )
        for i in range(len(notes))
    ]


def _stub_validate(
    raw: List[Dict[str, Any]], notes: List[str], battery: BatteryInput
) -> List[Directive]:
    """STUB — remove at integration.

    A deliberately thin stand-in for `app.guardrails.validate_interpretations`:
    it maps raw entries onto Directives and leaves every legality decision to
    `_final_schema_guard`. It does none of the repair work described in
    docs/04-guardrails.md — that is Kabya's module, and this exists only so the
    pipeline can be exercised end to end before it lands. `battery` is accepted
    purely to mirror the real signature.
    """
    by_index: Dict[int, Dict[str, Any]] = {}
    for position, entry in enumerate(raw or []):
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("note_index", position))
        except (TypeError, ValueError):
            idx = position
        by_index.setdefault(idx, entry)

    directives: List[Directive] = []
    for i in range(len(notes)):
        entry = by_index.get(i, {})
        dtype = str(entry.get("directive_type", "no_op")).strip().lower()
        adjustment = entry.get("structured_adjustment")
        if adjustment is None and dtype != "no_op":
            # The flat model schema from docs/05 carries the numbers inline.
            adjustment = {
                "hours": entry.get("hours"),
                "factor": entry.get("factor"),
                "minimum_energy_kwh": entry.get("minimum_energy_kwh"),
                "max_grid_kwh": entry.get("max_grid_kwh"),
            }
        directives.append(
            Directive(
                note_index=i,
                directive_type=dtype,
                adjustment=adjustment if isinstance(adjustment, dict) else None,
                explanation=str(entry.get("explanation") or "").strip()
                or "This note does not affect today's 24-hour energy schedule.",
                source="llm",
            )
        )
    return directives


def _final_schema_guard(
    directives: List[Directive], notes: List[str], battery: BatteryInput
) -> List[Directive]:
    """Last line of defence for the response schema (my 10 points, category 4).

    Guarantees exactly one entry per note, in note_index order, with legal
    `applies` semantics and a legal adjustment shape. It only ever **demotes** an
    entry to no_op — it never reorders, drops, or invents one, so it cannot
    undo Kabya's repair work. Every change is logged at WARNING: a guard that
    fires after integration means guardrails let something illegal through.
    """
    by_index: Dict[int, Directive] = {}
    for position, d in enumerate(directives or []):
        idx = d.note_index if isinstance(d.note_index, int) else position
        by_index.setdefault(idx, d)

    guarded: List[Directive] = []
    for i in range(len(notes)):
        d = by_index.get(i)
        if d is None:
            log.warning("schema guard: no interpretation for note %d; emitting no_op", i)
            guarded.append(
                Directive(
                    note_index=i,
                    directive_type="no_op",
                    adjustment=None,
                    explanation="This note does not affect today's 24-hour energy schedule.",
                    source="safe_default",
                )
            )
            continue

        dtype = str(d.directive_type).strip().lower()
        if dtype not in DIRECTIVE_TYPES:
            log.warning("schema guard: unsupported directive_type %r on note %d", dtype, i)
            dtype = "no_op"

        adjustment = None
        if dtype != "no_op":
            adjustment = _legal_adjustment(dtype, d.adjustment, battery)
            if adjustment is None:
                log.warning("schema guard: illegal %s adjustment on note %d; demoting", dtype, i)
                dtype = "no_op"

        explanation = (d.explanation or "").strip()
        if not explanation:
            explanation = (
                "This note does not affect today's 24-hour energy schedule."
                if dtype == "no_op"
                else f"Interpreted as a {dtype.replace('_', ' ')} directive."
            )

        guarded.append(
            Directive(
                note_index=i,
                directive_type=dtype,
                adjustment=adjustment,
                explanation=explanation,
                source=d.source,
            )
        )
    return guarded


def _legal_adjustment(
    dtype: str, adjustment: Any, battery: BatteryInput
) -> Optional[Dict[str, Any]]:
    """Return the exact structured_adjustment for `dtype`, or None if illegal."""
    if not isinstance(adjustment, dict):
        return None

    hours = _coerce_hours(adjustment.get("hours"))
    if not hours:
        return None

    if dtype in ("no_charge_window", "no_discharge_window"):
        return {"hours": hours}

    if dtype == "solar_reduction":
        factor = _coerce_number(adjustment.get("factor"))
        if factor is None or not 0.0 <= factor <= 1.0:
            return None
        return {"hours": hours, "factor": factor}

    if dtype == "minimum_battery_reserve":
        reserve = _coerce_number(adjustment.get("minimum_energy_kwh"))
        if reserve is None or reserve < 0:
            return None
        return {"hours": hours, "minimum_energy_kwh": min(reserve, battery.capacity_kwh)}

    if dtype == "max_grid_window":
        cap = _coerce_number(adjustment.get("max_grid_kwh"))
        if cap is None or cap < 0:
            return None
        return {"hours": hours, "max_grid_kwh": cap}

    return None


def _coerce_hours(value: Any) -> List[int]:
    """Unique ints 0..23, ascending. [] when nothing usable survives."""
    if value is None:
        return []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    out: set[int] = set()
    for item in value:
        number = _coerce_number(item)
        if number is None or number != int(number):
            continue
        hour = int(number)
        if 0 <= hour <= 23:
            out.add(hour)
    return sorted(out)


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().rstrip("%"))
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


# ==========================================================================
# Step 3 — constraints, solving, verification
# ==========================================================================


def _call_build_constraint_set(
    directives: List[Directive], hours: List[HourInput], battery: BatteryInput
) -> ConstraintSet:
    from app.guardrails import build_constraint_set

    try:
        return build_constraint_set(directives, hours, battery)
    except NotImplementedError:
        log.warning("STUB: app.guardrails.build_constraint_set is not implemented yet")
    except Exception:
        log.exception("build_constraint_set raised; folding directives locally")
    return _stub_constraint_set(directives, hours, battery)


def _stub_constraint_set(
    directives: List[Directive], hours: List[HourInput], battery: BatteryInput
) -> ConstraintSet:
    """STUB — remove at integration. The §5.3 fold, no more."""
    effective = [h.solar_kwh for h in hours]
    min_energy = [battery.minimum_energy_kwh] * HORIZON
    max_grid: List[Optional[float]] = [None] * HORIZON
    charge_blocked = [False] * HORIZON
    discharge_blocked = [False] * HORIZON
    applied: List[Directive] = []

    for d in directives:
        if not d.applies or not d.adjustment:
            continue
        window = d.adjustment.get("hours", [])
        if d.directive_type == "solar_reduction":
            for h in window:
                effective[h] *= float(d.adjustment["factor"])
        elif d.directive_type == "minimum_battery_reserve":
            for h in window:
                min_energy[h] = max(min_energy[h], float(d.adjustment["minimum_energy_kwh"]))
        elif d.directive_type == "no_charge_window":
            for h in window:
                charge_blocked[h] = True
        elif d.directive_type == "no_discharge_window":
            for h in window:
                discharge_blocked[h] = True
        elif d.directive_type == "max_grid_window":
            cap = float(d.adjustment["max_grid_kwh"])
            for h in window:
                max_grid[h] = cap if max_grid[h] is None else min(max_grid[h], cap)
        applied.append(d)

    return ConstraintSet(
        effective_solar_kwh=effective,
        min_energy_kwh=min_energy,
        max_grid_kwh=max_grid,
        charge_blocked=charge_blocked,
        discharge_blocked=discharge_blocked,
        applied=applied,
    )


async def _solve_with_ladder(
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[Directive],
    deadline: float,
    request_id: str,
) -> Tuple[List[HourPlan], str]:
    """Solve, degrading one directive at a time rather than returning garbage.

    Organizer scoring scenarios are guaranteed feasible, so infeasibility means
    our interpretation is wrong — not that the scenario is impossible. The ladder
    is documented in docs/03-optimizer-formulation.md. Kabya may later expose
    this as a helper in app/optimizer.py; until then the orchestration lives
    here, which is where the time budget is known.
    """
    applied = [d for d in directives if d.applies]
    droppable = sorted(applied, key=_drop_rank)

    attempts: List[Tuple[str, List[Directive]]] = [("all", applied)]
    for i in range(1, len(droppable) + 1):
        dropped = set(id(d) for d in droppable[:i])
        attempts.append((f"relaxed-{i}", [d for d in applied if id(d) not in dropped]))

    full_constraints = _call_build_constraint_set(directives, hours, battery)

    for label, subset in attempts:
        if _remaining(deadline) <= 0.25:
            log.warning("request_id=%s out of budget at rung %s", request_id, label)
            break

        constraints = (
            full_constraints
            if label == "all"
            else _call_build_constraint_set(subset, hours, battery)
        )

        try:
            plan = await _call_solver(hours, battery, constraints, _remaining(deadline))
        except _OptimizerMissing:
            log.warning("STUB: app.optimizer.solve is not implemented yet")
            break
        if plan is None:
            continue

        plan = _sanitise_plan(plan)
        problems = _call_verifier(plan, hours, battery, constraints)
        if not problems:
            if label != "all":
                log.warning("request_id=%s solved only after relaxing (%s)", request_id, label)
            return plan, label

        log.warning(
            "request_id=%s rung %s produced an invalid plan: %s",
            request_id,
            label,
            "; ".join(problems[:3]),
        )

    log.warning("request_id=%s falling back to the safe baseline plan", request_id)
    return _baseline(hours, battery, full_constraints), "baseline"


def _drop_rank(d: Directive) -> Tuple[int, int]:
    """Least trusted, most likely to be wrong, dropped first."""
    source_rank = {"fallback": 0, "stub": 0, "safe_default": 0, "llm_repaired": 1}.get(d.source, 2)
    type_rank = {
        "no_charge_window": 0,
        "no_discharge_window": 0,
        "max_grid_window": 1,
        "minimum_battery_reserve": 2,
        "solar_reduction": 3,
    }.get(d.directive_type, 4)
    return source_rank, type_rank


class _OptimizerMissing(RuntimeError):
    """STUB marker — app.optimizer.solve has not landed yet."""


async def _call_solver(
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
    budget_s: float,
) -> Optional[List[HourPlan]]:
    """Run the LP off the event loop, bounded by the remaining request budget.

    The LP is normally milliseconds, so the timeout is a backstop rather than a
    control: it exists so that one pathological solve cannot push the request
    past the judge's 30 s limit, which counts as an outright failure. A timed-out
    thread is abandoned rather than cancelled — Python cannot interrupt a running
    thread — but the ladder moves on immediately and the thread dies on its own.
    """
    from app.optimizer import InfeasibleError, solve

    try:
        return list(
            await asyncio.wait_for(
                asyncio.to_thread(solve, hours, battery, constraints),
                timeout=max(_MIN_SOLVE_BUDGET_S, budget_s),
            )
        )
    except NotImplementedError:
        raise _OptimizerMissing from None
    except asyncio.TimeoutError:
        log.warning("optimizer exceeded its %.2fs slice of the request budget", budget_s)
        return None
    except InfeasibleError:
        return None
    except Exception:
        log.exception("optimizer raised")
        return None


def _call_verifier(
    plan: List[HourPlan],
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[str]:
    from app.verifier import verify

    try:
        return list(verify(plan, hours, battery, constraints))
    except NotImplementedError:
        log.warning("STUB: app.verifier.verify is not implemented yet")
        return _stub_verify(plan, hours, battery, constraints)
    except Exception:
        log.exception("verifier raised; treating the plan as unverified")
        return _stub_verify(plan, hours, battery, constraints)


def _stub_verify(
    plan: List[HourPlan],
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[str]:
    """STUB — remove at integration.

    Structural checks only, so that a malformed plan can never reach the client.
    The full twelve-point replay against the judge's rules is Kabya's
    `app/verifier.py`; this is not a substitute for it.
    """
    problems: List[str] = []
    if len(plan) != HORIZON or sorted(e.hour for e in plan) != list(range(HORIZON)):
        return ["hourly_plan does not contain exactly one entry per hour 0..23"]

    demand = {h.hour: h.demand_kwh for h in hours}
    energy = battery.initial_energy_kwh
    for entry in sorted(plan, key=lambda e: e.hour):
        values = (entry.grid_kwh, entry.solar_used_kwh, entry.battery_kwh, entry.battery_energy_after_kwh)
        if any((not math.isfinite(v)) or v < -TOL for v in values):
            problems.append(f"hour {entry.hour}: non-finite or negative value")
        if entry.battery_action == "idle" and abs(entry.battery_kwh) > TOL:
            problems.append(f"hour {entry.hour}: idle with a non-zero battery_kwh")

        delta = entry.battery_kwh if entry.battery_action == "charge" else (
            -entry.battery_kwh if entry.battery_action == "discharge" else 0.0
        )
        energy += delta
        if abs(entry.battery_energy_after_kwh - energy) > TOL:
            problems.append(f"hour {entry.hour}: battery_energy_after_kwh does not follow the action")
            energy = entry.battery_energy_after_kwh

        charge = entry.battery_kwh if entry.battery_action == "charge" else 0.0
        discharge = entry.battery_kwh if entry.battery_action == "discharge" else 0.0
        balance = entry.grid_kwh + entry.solar_used_kwh + discharge - charge
        if abs(balance - demand.get(entry.hour, 0.0)) > TOL:
            problems.append(f"hour {entry.hour}: energy balance is off by {balance - demand.get(entry.hour, 0.0):.3f}")
        if entry.solar_used_kwh > constraints.effective_solar_kwh[entry.hour] + TOL:
            problems.append(f"hour {entry.hour}: uses more solar than is available")
        cap = constraints.max_grid_kwh[entry.hour]
        if cap is not None and entry.grid_kwh > cap + TOL:
            problems.append(f"hour {entry.hour}: grid import exceeds the directive cap")
        if not (constraints.min_energy_kwh[entry.hour] - TOL
                <= entry.battery_energy_after_kwh
                <= battery.capacity_kwh + TOL):
            problems.append(f"hour {entry.hour}: battery energy is outside its allowed band")

    if abs(plan[-1].battery_energy_after_kwh - battery.initial_energy_kwh) > TOL:
        problems.append("end-of-day battery energy does not return to the initial level")
    return problems


def _sanitise_plan(plan: List[HourPlan]) -> List[HourPlan]:
    """Clear solver dust before anything is verified or reported.

    Runs *before* verification so the verifier judges exactly the numbers the
    client will receive. Snapping only, never repair: it must not turn an
    invalid plan into one that merely looks valid.
    """
    cleaned: List[HourPlan] = []
    for entry in sorted(plan, key=lambda e: e.hour):
        battery_kwh = _clean(entry.battery_kwh)
        action = entry.battery_action
        # "idle" and a zero magnitude must agree: the judge checks both.
        if action == "idle" or battery_kwh == 0.0:
            action, battery_kwh = "idle", 0.0
        cleaned.append(
            HourPlan(
                hour=entry.hour,
                grid_kwh=_clean(entry.grid_kwh),
                solar_used_kwh=_clean(entry.solar_used_kwh),
                battery_action=action,
                battery_kwh=battery_kwh,
                battery_energy_after_kwh=_clean(entry.battery_energy_after_kwh),
            )
        )
    return cleaned


def _clean(value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    if abs(number) < _SNAP:
        return 0.0
    return round(max(number, 0.0), _ROUND)


# ==========================================================================
# Baselines — the bottom of the ladder
# ==========================================================================


def _baseline(
    hours: List[HourInput], battery: BatteryInput, constraints: ConstraintSet
) -> List[HourPlan]:
    from app.optimizer import safe_baseline_plan

    try:
        return _sanitise_plan(list(safe_baseline_plan(hours, battery, constraints)))
    except NotImplementedError:
        log.warning("STUB: app.optimizer.safe_baseline_plan is not implemented yet")
    except Exception:
        log.exception("safe_baseline_plan raised")
    return _stub_baseline(hours, battery, constraints)


def _stub_baseline(
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: Optional[ConstraintSet] = None,
) -> List[HourPlan]:
    """STUB — remove at integration.

    Grid covers demand, free solar is used up to the effective cap, the battery
    stays idle: energy balance holds and, because the battery never moves, its
    bounds and end-of-day neutrality hold trivially. It ignores grid caps and
    raised reserves — handling those is Kabya's `safe_baseline_plan`. This exists
    so that the pipeline always has something valid-shaped to return.
    """
    plan: List[HourPlan] = []
    for h in sorted(hours, key=lambda x: x.hour):
        available = (
            constraints.effective_solar_kwh[h.hour] if constraints is not None else h.solar_kwh
        )
        solar_used = max(0.0, min(available, h.demand_kwh))
        plan.append(
            HourPlan(
                hour=h.hour,
                grid_kwh=_clean(h.demand_kwh - solar_used),
                solar_used_kwh=_clean(solar_used),
                battery_action="idle",
                battery_kwh=0.0,
                battery_energy_after_kwh=_clean(battery.initial_energy_kwh),
            )
        )
    return plan


def _emergency_response(
    payload: ScenarioRequest, hours: List[HourInput], battery: BatteryInput
) -> OptimizeResponse:
    """Something unexpected broke. Answer with a valid-shaped, honest plan."""
    plan = _stub_baseline(hours, battery)
    directives = _all_no_op(
        payload.operator_notes, "This note does not affect today's 24-hour energy schedule."
    )
    total_grid, total_cost, peak = summarise_totals(
        plan, [h.tariff_bdt_per_kwh for h in hours]
    )
    return OptimizeResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=[d.to_interpretation() for d in directives],
        hourly_plan=plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak,
        plan_summary=(
            "Fell back to a grid-first schedule: demand is met from solar where "
            "available and from the grid otherwise, with the battery idle."
        ),
    )


def _remaining(deadline: float) -> float:
    return deadline - time.perf_counter()
