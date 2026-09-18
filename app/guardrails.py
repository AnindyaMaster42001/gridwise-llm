"""
Deterministic validation of untrusted model output.  OWNER: Kabya — Lane C.

The Problem Statement (§08) treats LLM output as untrusted structured data.
Nothing here may call a model; everything here must be pure and unit-testable.

Two responsibilities:
  1. validate_interpretations(): raw dicts -> List[Directive], exactly one per
     note, in note_index order, every shape guaranteed legal.
  2. build_constraint_set(): List[Directive] -> ConstraintSet, the per-hour
     numbers the optimizer and verifier both read.

Repair-not-reject policy
------------------------
A near-miss from the model should be repaired, because a demoted note loses all
five "relevance" points for that case:
  * hours unsorted / duplicated      -> sort + dedupe
  * hours as floats or strings       -> coerce to int
  * hour out of 0..23                -> drop that hour (keep the rest)
  * factor given as 20 or "20%"      -> 0.2
  * factor expressed as the reduction rather than the remainder -> only convert
    when the model explicitly said "reduction"; otherwise trust the number
  * reserve above capacity           -> clamp to capacity
  * missing note_index               -> positional index
Anything still illegal after repair, or any unknown directive_type, becomes
no_op — never a guess at a different directive, never a dropped entry.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from app.schemas import (
    DIRECTIVE_TYPES,
    HORIZON,
    BatteryInput,
    ConstraintSet,
    Directive,
    HourInput,
)

log = logging.getLogger(__name__)

REQUIRED_KEYS: Dict[str, Tuple[str, ...]] = {
    "solar_reduction": ("hours", "factor"),
    "minimum_battery_reserve": ("hours", "minimum_energy_kwh"),
    "no_charge_window": ("hours",),
    "no_discharge_window": ("hours",),
    "max_grid_window": ("hours", "max_grid_kwh"),
    "no_op": (),
}

NO_OP = "no_op"

# Sources, weakest evidence first. The infeasibility ladder drops in this order.
SOURCES = ("fallback", "llm_repaired", "llm", "safe_default")

# Keys a weak model might use instead of the contract names. Looking these up is
# free; guessing a *directive type* from note text is not allowed and is not done.
_HOUR_KEYS = ("hours", "hour", "affected_hours", "window", "hours_affected")
_FACTOR_KEYS = ("factor", "solar_factor", "remaining_fraction", "solar_fraction")
_RESERVE_KEYS = ("minimum_energy_kwh", "min_energy_kwh", "reserve_kwh", "minimum_kwh")
_GRID_KEYS = ("max_grid_kwh", "grid_cap_kwh", "max_grid", "grid_limit_kwh")

# Wrapper keys a model uses when it returns an object instead of a bare array.
_LIST_WRAPPER_KEYS = (
    "directive_interpretation",
    "directives",
    "interpretations",
    "interpretation",
    "results",
    "notes",
)

_SNAP = 1e-9

# "80% reduction", "20 percent drop", "cut by 80%", "reduction of 80%".
# Deliberately does NOT match "drops to 20%" / "reduced to 20%", where the number
# already is the remaining fraction.
_REDUCTION_PATTERNS = (
    re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:%|percent|per\s*cent)?\s*"
        r"(?:reduction|decrease|drop|loss|cut|curtailment)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reduc\w*|decreas\w*|drop\w*|cut|lower\w*|curtail\w*|down|fall\w*)\s+"
        r"(?:by|of)\s+(?:about\s+|roughly\s+|around\s+|approximately\s+|nearly\s+)?"
        r"(\d+(?:\.\d+)?)\s*(?:%|percent|per\s*cent)?",
        re.IGNORECASE,
    ),
)


# --------------------------------------------------------------------------
# Scalar coercion
# --------------------------------------------------------------------------


def _coerce_float(value: Any, percent_scales: bool = True) -> Optional[float]:
    """Best-effort float. Returns None when the value is not usable.

    `percent_scales` divides a trailing-"%" string by 100. That is right for a
    solar factor ("20%" -> 0.2) and wrong for a reserve ("50%" of a 200 kWh
    battery is 100 kWh, not 0.5), so reserves and grid caps pass False and are
    left alone — see the "never auto-convert a reserve" rule in docs/04.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if math.isfinite(v) else None
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return None
        is_pct = s.endswith("%")
        if is_pct:
            s = s[:-1].strip()
        s = re.sub(r"(?i)\s*(kwh|kw|bdt|percent|per\s*cent)\s*$", "", s).strip()
        try:
            v = float(s)
        except ValueError:
            return None
        if not math.isfinite(v):
            return None
        return v / 100.0 if (is_pct and percent_scales) else v
    return None


def _is_integral(v: float) -> bool:
    return abs(v - round(v)) <= 1e-9


def _round6(v: float) -> float:
    return round(v + 0.0, 6)


# --------------------------------------------------------------------------
# Stage 3 — hours
# --------------------------------------------------------------------------


def _hour_tokens(value: Any) -> List[Any]:
    """Flatten whatever the model sent into candidate hour tokens."""
    if value is None:
        return []
    if isinstance(value, dict):
        start = _coerce_float(_first_present(value, ("start", "from", "start_hour", "begin")))
        end = _coerce_float(_first_present(value, ("end", "to", "end_hour", "finish")))
        if start is None or end is None or not _is_integral(start) or not _is_integral(end):
            return []
        s, e = int(round(start)), int(round(end))
        # Windows are start-inclusive / end-exclusive (spec digest §6).
        if e < s:  # wraps midnight
            return list(range(s, HORIZON)) + list(range(0, e))
        return list(range(s, e))
    if isinstance(value, (list, tuple, set, frozenset)):
        out: List[Any] = []
        for item in value:
            out.extend(_hour_tokens(item))
        return out
    if isinstance(value, str):
        return [t for t in re.split(r"[^0-9.:]+", value) if t]
    return [value]


def _coerce_hour(token: Any) -> Optional[int]:
    if isinstance(token, bool):
        return None
    if isinstance(token, int):
        v: float = float(token)
    elif isinstance(token, float):
        v = token
    elif isinstance(token, str):
        s = token.strip()
        if ":" in s:  # "13:00"
            s = s.split(":", 1)[0]
        f = _coerce_float(s, percent_scales=False)
        if f is None:
            return None
        v = f
    else:
        return None
    if not math.isfinite(v) or not _is_integral(v):
        return None
    h = int(round(v))
    return h if 0 <= h < HORIZON else None


def normalise_hours(value: Any) -> List[int]:
    """Coerce to unique ints in 0..23, ascending. Returns [] if nothing survives."""
    seen = set()
    for token in _hour_tokens(value):
        h = _coerce_hour(token)
        if h is not None:
            seen.add(h)
    return sorted(seen)


# --------------------------------------------------------------------------
# Stage 4 — numbers
# --------------------------------------------------------------------------


def _explanation_claims_reduction(explanation: str, candidate: float) -> bool:
    """True only when the model said "reduction/drop of X%" AND emitted that X.

    This is the one place we override the model's own number, so the bar is
    deliberately high: a bare "reduction" with no matching percentage, or a
    percentage that does not match what was emitted, leaves the value alone.
    """
    if not explanation or not isinstance(explanation, str):
        return False
    for pattern in _REDUCTION_PATTERNS:
        for match in pattern.finditer(explanation):
            try:
                stated = float(match.group(1))
            except (TypeError, ValueError):
                continue
            if abs(stated / 100.0 - candidate) <= 1e-6:
                return True
    return False


def normalise_factor(value: Any, explanation: str = "") -> Optional[float]:
    """Coerce to a usable-fraction in [0, 1], or None if impossible."""
    v = _coerce_float(value, percent_scales=True)
    if v is None or v < 0.0:
        return None
    if v > 1.0:
        # A whole-number percentage is the one unambiguous rescale: 20 -> 0.2.
        # 1.4 is not a percentage and not a fraction, so it is not repairable.
        if v <= 100.0 and _is_integral(v):
            v = v / 100.0
        else:
            return None
    if _explanation_claims_reduction(explanation, v):
        log.info("guardrails: factor %.4f restated as remaining %.4f", v, 1.0 - v)
        v = 1.0 - v
    return _round6(min(1.0, max(0.0, v)))


def normalise_reserve(value: Any, battery: BatteryInput) -> Optional[float]:
    """Coerce to a kWh reserve in [0, capacity], or None if impossible."""
    v = _coerce_float(value, percent_scales=False)
    if v is None or v < 0.0:
        return None
    capacity = float(battery.capacity_kwh)
    if v > capacity:
        log.info("guardrails: reserve %.3f above capacity %.3f, clamped", v, capacity)
        v = capacity
    elif v <= 1.0 and capacity > 10.0:
        # Probably a fraction the prompt failed to resolve — but a 1 kWh reserve
        # is also legal, and converting a correct value is worse than keeping a
        # harmless one. Log, do not convert (docs/04).
        log.warning(
            "guardrails: suspicious reserve %.4f kWh on a %.1f kWh battery; used as-is",
            v,
            capacity,
        )
    return _round6(v)


def normalise_grid_cap(value: Any) -> Optional[float]:
    """Coerce to a non-negative kWh cap, or None if impossible. No upper clamp."""
    v = _coerce_float(value, percent_scales=False)
    if v is None or v < 0.0:
        return None
    return _round6(v)


# --------------------------------------------------------------------------
# Stages 1, 2, 5 — one Directive per note
# --------------------------------------------------------------------------


def _first_present(entry: Dict[str, Any], names: Tuple[str, ...]) -> Any:
    for name in names:
        if name in entry and entry[name] is not None:
            return entry[name]
    return None


def _field(entry: Dict[str, Any], names: Tuple[str, ...]) -> Any:
    """Look in the flat entry first, then inside `structured_adjustment`."""
    value = _first_present(entry, names)
    if value is not None:
        return value
    nested = entry.get("structured_adjustment")
    if isinstance(nested, dict):
        return _first_present(nested, names)
    return None


def _normalise_type(value: Any) -> Optional[str]:
    """Map to one of the six supported types, or None if unrecognised."""
    if not isinstance(value, str):
        return None
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    return key if key in DIRECTIVE_TYPES else None


def _normalise_source(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip().lower() in SOURCES:
        return value.strip().lower()
    return None


def _clean_explanation(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:400]


def _as_entry_list(raw: Any) -> List[Any]:
    """Unwrap the shapes a model returns instead of a bare list."""
    if isinstance(raw, dict):
        for key in _LIST_WRAPPER_KEYS:
            inner = raw.get(key)
            if isinstance(inner, list):
                return list(inner)
        return [raw]  # a single interpretation object
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def _note_index_of(value: Any, count: int) -> Optional[int]:
    if isinstance(value, bool):
        return None
    f = _coerce_float(value, percent_scales=False)
    if f is None or not _is_integral(f):
        return None
    i = int(round(f))
    return i if 0 <= i < count else None


def _align_entries(raw: Any, count: int) -> List[Optional[Dict[str, Any]]]:
    """Stage 1. One slot per note: honour note_index, then fill positionally."""
    slots: List[Optional[Dict[str, Any]]] = [None] * count
    leftovers: List[Optional[Dict[str, Any]]] = []

    for entry in _as_entry_list(raw):
        if isinstance(entry, dict):
            idx = _note_index_of(entry.get("note_index"), count)
            if idx is not None and slots[idx] is None:
                slots[idx] = entry
                continue
            leftovers.append(entry)
        else:
            leftovers.append(None)

    spare = iter(leftovers)
    for i in range(count):
        if slots[i] is None:
            slots[i] = next(spare, None)
    return slots


def _demoted(index: int, reason: str, explanation: str, source: str) -> Directive:
    log.info("guardrails: note %d demoted to no_op (%s)", index, reason)
    return Directive(
        note_index=index,
        directive_type=NO_OP,
        adjustment=None,
        explanation=explanation
        or f"Interpretation could not be validated ({reason}); treated as not affecting the schedule.",
        source=source,
    )


def _build_directive(index: int, entry: Optional[Dict[str, Any]], battery: BatteryInput) -> Directive:
    if not isinstance(entry, dict):
        return Directive(
            note_index=index,
            directive_type=NO_OP,
            adjustment=None,
            explanation="No interpretation was returned for this note; treated as not affecting the schedule.",
            source="safe_default",
        )

    explanation = _clean_explanation(entry.get("explanation"))
    declared_source = _normalise_source(entry.get("source")) or "llm"
    dtype = _normalise_type(entry.get("directive_type"))

    def repaired_source() -> str:
        return declared_source if declared_source == "fallback" else "llm_repaired"

    if dtype is None:
        return _demoted(index, "unsupported directive_type", "", repaired_source())
    if dtype == NO_OP:
        return Directive(
            note_index=index,
            directive_type=NO_OP,
            adjustment=None,
            explanation=explanation or "This note does not affect today's 24-hour energy schedule.",
            source=declared_source,
        )

    raw_hours = _field(entry, _HOUR_KEYS)
    hours = normalise_hours(raw_hours)
    if not hours:
        return _demoted(index, "no usable hours", "", repaired_source())
    hours_repaired = raw_hours != hours

    if dtype == "solar_reduction":
        factor = normalise_factor(_field(entry, _FACTOR_KEYS), explanation)
        if factor is None:
            return _demoted(index, "factor outside [0, 1]", "", repaired_source())
        adjustment: Dict[str, Any] = {"hours": hours, "factor": factor}
        value_repaired = factor != _coerce_float(_field(entry, _FACTOR_KEYS), percent_scales=True)
    elif dtype == "minimum_battery_reserve":
        reserve = normalise_reserve(_field(entry, _RESERVE_KEYS), battery)
        if reserve is None:
            return _demoted(index, "reserve not a usable kWh value", "", repaired_source())
        adjustment = {"hours": hours, "minimum_energy_kwh": reserve}
        value_repaired = reserve != _coerce_float(_field(entry, _RESERVE_KEYS), percent_scales=False)
    elif dtype == "max_grid_window":
        cap = normalise_grid_cap(_field(entry, _GRID_KEYS))
        if cap is None:
            return _demoted(index, "grid cap not a usable kWh value", "", repaired_source())
        adjustment = {"hours": hours, "max_grid_kwh": cap}
        value_repaired = cap != _coerce_float(_field(entry, _GRID_KEYS), percent_scales=False)
    else:  # no_charge_window | no_discharge_window
        adjustment = {"hours": hours}
        value_repaired = False

    # Stage 5: emit exactly the keys this type requires, nothing else.
    assert tuple(adjustment) == REQUIRED_KEYS[dtype], dtype

    source = declared_source
    if declared_source != "fallback" and (hours_repaired or value_repaired):
        source = "llm_repaired"

    return Directive(
        note_index=index,
        directive_type=dtype,
        adjustment=adjustment,
        explanation=explanation or f"Applied {dtype} to hours {hours}.",
        source=source,
    )


def validate_interpretations(
    raw: List[Dict[str, Any]],
    notes: List[str],
    battery: BatteryInput,
) -> List[Directive]:
    """Return exactly `len(notes)` Directives, index 0..N-1, all legal.

    Never raises: a completely unusable `raw` yields all-no_op directives.
    """
    count = len(notes)
    if count == 0:
        return []
    try:
        slots = _align_entries(raw, count)
    except Exception:  # pragma: no cover - alignment is total, this is a backstop
        log.exception("guardrails: alignment failed, falling back to all-no_op")
        slots = [None] * count

    directives: List[Directive] = []
    for index in range(count):
        try:
            directives.append(_build_directive(index, slots[index], battery))
        except Exception:  # pragma: no cover - never let one bad entry take the request down
            log.exception("guardrails: note %d failed validation, demoting to no_op", index)
            directives.append(
                Directive(
                    note_index=index,
                    directive_type=NO_OP,
                    adjustment=None,
                    explanation="Interpretation could not be validated; treated as not affecting the schedule.",
                    source="safe_default",
                )
            )
    return directives


# --------------------------------------------------------------------------
# Stage 6 — the ConstraintSet
# --------------------------------------------------------------------------


def build_constraint_set(
    directives: List[Directive],
    hours: List[HourInput],
    battery: BatteryInput,
) -> ConstraintSet:
    """Fold applied directives into per-hour arrays (Problem Statement §5.3).

      solar_reduction          effective_solar[h] = solar[h] * factor   (multiply
                               if several land on one hour)
      minimum_battery_reserve  min_energy[h] = max(base_minimum, directive value)
      no_charge_window         charge_blocked[h] = True
      no_discharge_window      discharge_blocked[h] = True
      max_grid_window          max_grid[h] = min(existing cap, directive value)
    """
    ordered = sorted(hours, key=lambda h: h.hour)
    capacity = float(battery.capacity_kwh)
    base_minimum = float(battery.minimum_energy_kwh)

    effective_solar = [float(h.solar_kwh) for h in ordered]
    min_energy = [base_minimum] * HORIZON
    max_grid: List[Optional[float]] = [None] * HORIZON
    charge_blocked = [False] * HORIZON
    discharge_blocked = [False] * HORIZON
    applied: List[Directive] = []

    for directive in directives or []:
        adjustment = directive.adjustment
        if not directive.applies or not isinstance(adjustment, dict):
            continue
        window = [h for h in adjustment.get("hours", []) if isinstance(h, int) and 0 <= h < HORIZON]
        if not window:
            continue

        kind = directive.directive_type
        if kind == "solar_reduction":
            factor = float(adjustment["factor"])
            for h in window:
                effective_solar[h] *= factor
        elif kind == "minimum_battery_reserve":
            reserve = float(adjustment["minimum_energy_kwh"])
            for h in window:
                min_energy[h] = max(min_energy[h], reserve)
        elif kind == "no_charge_window":
            for h in window:
                charge_blocked[h] = True
        elif kind == "no_discharge_window":
            for h in window:
                discharge_blocked[h] = True
        elif kind == "max_grid_window":
            cap = float(adjustment["max_grid_kwh"])
            for h in window:
                max_grid[h] = cap if max_grid[h] is None else min(max_grid[h], cap)
        else:  # pragma: no cover - validate_interpretations cannot produce this
            continue

        applied.append(directive)

    effective_solar = [_round6(max(0.0, value)) for value in effective_solar]
    # A floor above capacity can never be met and would poison the LP; the base
    # minimum is the organizer's own number, so clamping only ever helps.
    for h in range(HORIZON):
        if min_energy[h] > capacity:
            log.warning(
                "guardrails: hour %d floor %.3f exceeds capacity %.3f, clamped", h, min_energy[h], capacity
            )
            min_energy[h] = capacity
        min_energy[h] = _round6(min_energy[h])

    return ConstraintSet(
        effective_solar_kwh=effective_solar,
        min_energy_kwh=min_energy,
        max_grid_kwh=max_grid,
        charge_blocked=charge_blocked,
        discharge_blocked=discharge_blocked,
        applied=applied,
    )
