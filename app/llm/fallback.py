"""
Deterministic backup interpreter.  OWNER: Ninad — Lane B.

Used ONLY when every LLM provider has failed. Its job is to keep the service
answering with something plausible instead of 500-ing, not to replace the model.
Keep it clearly separated so an artifact reviewer can see the LLM is the primary
path (the Guide penalises phrase-matching as the *sole* interpreter).

Report its use honestly: set Directive.source = "fallback" and say so in the
explanation text.

Layout of this module
---------------------
    parse_hours      time expressions   -> ascending hours, end-exclusive
    parse_factor     percentages/words  -> the fraction of solar that REMAINS
    parse_reserve    kWh or % of pack   -> absolute kWh
    parse_grid_cap   "no more than N"   -> kWh
    rule_based_interpret                -> one raw dict per note

Every parser returns None when it is not confident, and an unclassified note
becomes `no_op`. Inventing a directive is worse than missing one, because a
wrong hard constraint can make an otherwise valid schedule illegal.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.schemas import BatteryInput

# --------------------------------------------------------------------------
# Time expressions
# --------------------------------------------------------------------------

_WORD_HOURS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_WORD_COUNTS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_HOUR_WORDS = "|".join(_WORD_HOURS)


def _time_pattern(prefix: str) -> str:
    """A single clock reference, with group names prefixed so two can coexist."""
    return (
        rf"(?:(?P<{prefix}word>noon|midday|midnight|{_HOUR_WORDS})"
        rf"|(?P<{prefix}h>\d{{1,2}})(?::(?P<{prefix}min>\d{{2}}))?"
        rf"(?:\s*(?P<{prefix}ap>a\.?m\.?|p\.?m\.?))?)"
    )


_CONNECTOR = r"(?:\s*(?:through\s+to|up\s+to|until|till|til|through|thru|to|-|–|—)\s*)"

_BETWEEN_RE = re.compile(
    rf"\bbetween\s+{_time_pattern('a')}\s+and\s+{_time_pattern('b')}",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(
    rf"(?:\bfrom\s+|\bstarting\s+(?:at|from)\s+)?{_time_pattern('a')}"
    rf"{_CONNECTOR}{_time_pattern('b')}",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    rf"(?:for\s+|the\s+)?(?P<count>\d{{1,2}}|{'|'.join(_WORD_COUNTS)})\s*-?\s*hours?"
    rf"(?:\s+(?:long|window))?"
    rf"\s*(?:starting|beginning|commencing)?\s*(?:at|from)\s+{_time_pattern('a')}",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(
    rf"(?:\bat\b|\bduring\s+the\b|\bfor\s+the\b|\bin\s+the\b)\s+{_time_pattern('a')}"
    rf"(?:\s*(?:hour|slot|interval))?",
    re.IGNORECASE,
)
_ALL_DAY_RE = re.compile(
    r"\b(?:all\s+day|whole\s+day|entire\s+day|throughout\s+the\s+day|"
    r"round\s+the\s+clock|24\s*hours|all\s+24\s+hours|every\s+hour)\b",
    re.IGNORECASE,
)

# A clock reference we refuse to read as a time, because the number belongs to
# something else: "155 kWh", "80%", "hour 13" is fine but "13 kWh" is not.
_UNIT_AFTER = re.compile(r"\s*(?:kwh|kw|%|percent|per\s?cent|bdt|taka)", re.IGNORECASE)


class _Clock:
    """One parsed clock reference, before AM/PM inheritance is resolved."""

    __slots__ = ("value", "meridiem", "explicit", "is_midnight")

    def __init__(
        self,
        value: int,
        meridiem: Optional[str],
        explicit: bool,
        is_midnight: bool = False,
    ) -> None:
        self.value = value
        self.meridiem = meridiem
        self.explicit = explicit
        self.is_midnight = is_midnight


def _clock(match: re.Match, prefix: str) -> Optional[_Clock]:
    groups = match.groupdict()
    word = groups.get(f"{prefix}word")
    if word:
        word = word.lower()
        if word in {"noon", "midday"}:
            return _Clock(12, None, True)
        if word == "midnight":
            return _Clock(0, None, True, is_midnight=True)
        return _Clock(_WORD_HOURS[word], None, False)

    raw_hour = groups.get(f"{prefix}h")
    if raw_hour is None:
        return None
    hour = int(raw_hour)
    if hour > 24:
        return None
    meridiem = (groups.get(f"{prefix}ap") or "").replace(".", "").lower() or None
    has_colon = groups.get(f"{prefix}min") is not None
    minutes = int(groups.get(f"{prefix}min") or 0)
    if minutes not in (0, 30):  # ":45" is not a whole-hour boundary we can use
        return None
    # 13:00, 17, 24 and anything with a colon are unambiguous 24-hour clock.
    explicit = bool(meridiem) or has_colon or hour > 12 or hour == 0
    return _Clock(hour, meridiem, explicit)


def _apply_meridiem(hour: int, meridiem: Optional[str]) -> int:
    if meridiem == "pm":
        return hour if hour == 12 else hour + 12
    if meridiem == "am":
        return 0 if hour == 12 else hour
    return hour


def _resolve_pair(start: _Clock, end: _Clock) -> Tuple[int, int]:
    """Turn two clock references into absolute start/end hours, end-exclusive.

    Handles the three ways campus English leaves AM/PM implicit:
      "1-3 PM"            -> the meridiem on the end applies to the start
      "from 11 until 2 PM" -> 11 cannot be PM here, so it is AM
      "from one until three" -> no meridiem at all; campus notes mean daytime
    """
    if start.meridiem and not end.meridiem and not end.explicit:
        end.meridiem = (
            start.meridiem if end.value >= start.value else _other(start.meridiem)
        )
    if end.meridiem and not start.meridiem and not start.explicit:
        start.meridiem = (
            end.meridiem if start.value <= end.value else _other(end.meridiem)
        )

    start_hour = _apply_meridiem(start.value, start.meridiem)
    end_hour = _apply_meridiem(end.value, end.meridiem)

    # Bare small numbers on a campus mean the afternoon: "from one until three"
    # is 13:00-15:00, not 01:00-03:00.
    if not start.explicit and not start.meridiem and 1 <= start_hour <= 6:
        start_hour += 12
        if not end.explicit and not end.meridiem and end_hour <= 6:
            end_hour += 12
    elif not end.explicit and not end.meridiem and 1 <= end_hour <= 6 and end_hour < start_hour:
        end_hour += 12

    # "until midnight" closes the day rather than opening it.
    if end.is_midnight and start_hour > 0:
        end_hour = 24
    return start_hour, end_hour


def _other(meridiem: str) -> str:
    return "am" if meridiem == "pm" else "pm"


def _window(start_hour: int, end_hour: int) -> Optional[List[int]]:
    """Start-inclusive, end-exclusive, ascending, wrap-aware."""
    if end_hour <= start_hour:
        end_hour += 24
    span = end_hour - start_hour
    if span <= 0 or span > 24:
        return None
    return sorted({hour % 24 for hour in range(start_hour, end_hour)})


def _followed_by_unit(text: str, match: re.Match) -> bool:
    return bool(_UNIT_AFTER.match(text[match.end() :]))


def parse_hours(text: str) -> Optional[List[int]]:
    """Extract the affected hours, ascending and end-exclusive, or None."""
    for regex in (_BETWEEN_RE, _RANGE_RE):
        for match in regex.finditer(text):
            if _followed_by_unit(text, match):
                continue
            start, end = _clock(match, "a"), _clock(match, "b")
            if start is None or end is None:
                continue
            hours = _window(*_resolve_pair(start, end))
            if hours:
                return hours

    for match in _DURATION_RE.finditer(text):
        raw_count = match.group("count").lower()
        count = _WORD_COUNTS.get(raw_count, None)
        if count is None:
            try:
                count = int(raw_count)
            except ValueError:
                continue
        start = _clock(match, "a")
        if start is None or not 1 <= count <= 24:
            continue
        begin = _apply_meridiem(start.value, start.meridiem)
        if not start.explicit and not start.meridiem and 1 <= begin <= 6:
            begin += 12
        hours = _window(begin, begin + count)
        if hours:
            return hours

    if _ALL_DAY_RE.search(text):
        return list(range(24))

    for match in _SINGLE_RE.finditer(text):
        if _followed_by_unit(text, match):
            continue
        single = _clock(match, "a")
        if single is None:
            continue
        hour = _apply_meridiem(single.value, single.meridiem)
        if not single.explicit and not single.meridiem and 1 <= hour <= 6:
            hour += 12
        if 0 <= hour <= 23:
            return [hour]
    return None


# --------------------------------------------------------------------------
# Fractions
# --------------------------------------------------------------------------

_WORD_FRACTIONS = {
    "half": 0.5,
    "one half": 0.5,
    "a half": 0.5,
    "third": 1 / 3,
    "one third": 1 / 3,
    "a third": 1 / 3,
    "two thirds": 2 / 3,
    "quarter": 0.25,
    "one quarter": 0.25,
    "a quarter": 0.25,
    "fourth": 0.25,
    "three quarters": 0.75,
    "fifth": 0.2,
    "one fifth": 0.2,
    "a fifth": 0.2,
    "two fifths": 0.4,
    "three fifths": 0.6,
    "four fifths": 0.8,
    "sixth": 1 / 6,
    "eighth": 0.125,
    "tenth": 0.1,
    "three tenths": 0.3,
    "seven tenths": 0.7,
}
# Longest first so "two thirds" beats "third".
_FRACTION_RE = re.compile(
    r"\b(?P<word>"
    + "|".join(
        sorted((k.replace(" ", r"[\s-]+") for k in _WORD_FRACTIONS), key=len, reverse=True)
    )
    + r")\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(
    r"(?P<pct>\d{1,3}(?:\.\d+)?)\s*(?:%|per\s?cent|percent)", re.IGNORECASE
)

# "80% reduction", "60% loss", "a 70% cut"
_COMPLEMENT_AFTER = re.compile(
    r"^\s*(?:point\s+\d+\s*)?(?:reduction|cut|loss|drop|decrease|decline|less|lower|"
    r"down|curtailment|derate|derating|dip)\b",
    re.IGNORECASE,
)
_BY_BEFORE = re.compile(r"\bby\s*(?:about|around|roughly|approximately|some|nearly)?\s*$", re.IGNORECASE)
_HALVED_RE = re.compile(r"\bhalv(?:e|es|ed|ing)\b", re.IGNORECASE)
_TOTAL_LOSS_RE = re.compile(
    r"\b(?:no\s+(?:usable\s+)?(?:solar|pv|generation|output)|zero\s+(?:solar|pv|output|generation)"
    r"|solar\s+(?:is\s+)?(?:completely\s+)?(?:offline|unavailable|out|down|lost)"
    r"|panels?\s+(?:are\s+)?(?:completely\s+)?(?:offline|out\s+of\s+service|disconnected|covered)"
    r"|(?:pv|solar)\s+array\s+(?:is\s+)?(?:isolated|offline)"
    r"|lose\s+all\s+(?:solar|pv|generation))\b",
    re.IGNORECASE,
)


def parse_factor(text: str) -> Optional[float]:
    """The fraction of solar that REMAINS, in [0, 1], or None.

    "80% reduction" and "drops to 20%" both mean 0.2; the difference is the
    preposition, so that is what we read.
    """
    if _TOTAL_LOSS_RE.search(text):
        return 0.0
    if _HALVED_RE.search(text):
        return 0.5

    best: Optional[float] = None
    for match in _PERCENT_RE.finditer(text):
        value = float(match.group("pct")) / 100.0
        if not 0.0 <= value <= 1.0:
            continue
        best = _orient(text, match, value)
        break

    if best is None:
        for match in _FRACTION_RE.finditer(text):
            key = re.sub(r"[\s-]+", " ", match.group("word").lower())
            value = _WORD_FRACTIONS.get(key)
            if value is None:
                continue
            best = _orient(text, match, value)
            break

    if best is None:
        return None
    return round(min(1.0, max(0.0, best)), 4)


def _orient(text: str, match: re.Match, value: float) -> float:
    """Decide whether the matched quantity is what remains or what is lost."""
    before = text[max(0, match.start() - 40) : match.start()]
    after = text[match.end() : match.end() + 30]
    if _BY_BEFORE.search(before) or _COMPLEMENT_AFTER.match(after):
        return 1.0 - value
    return value


# --------------------------------------------------------------------------
# kWh quantities
# --------------------------------------------------------------------------

_KWH_RE = re.compile(r"(?P<value>\d{1,6}(?:\.\d+)?)\s*(?:kwh|kw\s?h|kilowatt[-\s]?hours?)", re.IGNORECASE)
def parse_reserve(text: str, capacity_kwh: float) -> Optional[float]:
    """Absolute kWh floor, resolving a percentage against the battery capacity."""
    match = _KWH_RE.search(text)
    if match:
        return float(match.group("value"))

    fraction: Optional[float] = None
    percent = _PERCENT_RE.search(text)
    if percent:
        fraction = float(percent.group("pct")) / 100.0
    else:
        word = _FRACTION_RE.search(text)
        if word:
            fraction = _WORD_FRACTIONS.get(
                re.sub(r"[\s-]+", " ", word.group("word").lower())
            )
    if fraction is None or not 0.0 <= fraction <= 1.0:
        return None
    return round(fraction * float(capacity_kwh), 4)


def parse_grid_cap(text: str) -> Optional[float]:
    match = _KWH_RE.search(text)
    return float(match.group("value")) if match else None


# --------------------------------------------------------------------------
# Directive classification
# --------------------------------------------------------------------------

_NEGATION = (
    r"(?:not|no|never|cannot|can't|cant|won't|wont|must\s+not|may\s+not|do\s+not|don't|"
    r"unable|avoid|refrain|prohibit\w*|forbidden|disabled?|disallow\w*|unavailable|"
    r"offline|isolated|out\s+of\s+service|locked\s+out|blocked|suspend\w*|halt\w*|"
    r"stop\w*|paused?|inhibit\w*|withheld|skip|idle|prevent\w*|reject\w*|refus\w*|declin\w*|bar(?:red|s)?|keep\s+(?:\w+\s+){0,3}from)"
)

_DISCHARGE_WORD = r"(?:discharg\w*|supply(?:ing)?\s+(?:any\s+)?load|export\w*\s+from\s+the\s+battery|draw\w*\s+down\b|draw\w*\s+from\s+the\s+(?:battery|pack)|battery\s+output|drain\w*|deplet\w*|deliver\w*\s+(?:any\s+)?(?:energy|power))"
_CHARGE_WORD = (
    r"(?:(?<!dis)charg\w*|take\s+(?:a\s+)?charge|accept\w*\s+(?:a\s+)?(?:charge|energy)"
    r"|takes?\s+no\s+energy\s+in|absorb\w*|top\s*-?\s*up|replenish\w*"
    r"|(?:put|push|store|stored|feed|send|inject)\w*\s+(?:any\s+)?energy\s+(?:in|into)"
    r"|energy\s+(?:may|can|will|is|are|to)?\s*(?:be\s+)?(?:put|pushed|stored|fed|sent|injected|added)\s+(?:in|into)"
    r"|energy\s+into\s+the\s+(?:battery|pack|bess|storage))"
)

_NO_DISCHARGE_RE = re.compile(
    rf"(?:{_NEGATION}[^.;]{{0,60}}?{_DISCHARGE_WORD}|{_DISCHARGE_WORD}[^.;]{{0,40}}?{_NEGATION})",
    re.IGNORECASE,
)
_NO_CHARGE_RE = re.compile(
    rf"(?:{_NEGATION}[^.;]{{0,60}}?{_CHARGE_WORD}|{_CHARGE_WORD}[^.;]{{0,40}}?{_NEGATION})",
    re.IGNORECASE,
)

_RESERVE_RE = re.compile(
    r"\b(?:at\s+least|no\s+less\s+than|not\s+less\s+than|no\s+lower\s+than|"
    r"(?:fall|falls|drop|drops|dip|dips|go|goes|sink|sinks|slip|slips)\s+"
    r"(?:below|under|beneath)|"
    r"minimum\s+of|min\.?\s+of|keep|maintain|hold|retain|preserve|reserve|reserved|"
    r"stay\s+(?:at\s+or\s+)?above|remain\s+(?:at\s+or\s+)?above|remain\s+(?:stored|in\s+the\s+battery)|"
    r"still\s+stored|floor\s+of|or\s+(?:more|above|higher)|at\s+or\s+(?:more|above))\b",
    re.IGNORECASE,
)
_BATTERY_WORD_RE = re.compile(
    r"\b(?:battery|batteries|pack|bess|storage|stored|state\s+of\s+charge|soc|"
    r"reserve|charge\s+level|energy\s+level|capacity)\b",
    re.IGNORECASE,
)

_GRID_WORD_RE = re.compile(
    r"\b(?:grid|import\w*|intake|in-?take|feeder|transformer|substation|incomer|"
    r"mains|utility|supply\s+point|metered\s+draw|purchased\s+(?:power|energy)|contracted\s+demand|demand\s+ceiling|sanctioned\s+load|connection\s+(?:limit|capacity)|"
    r"drawn\s+from\s+the\s+grid|from\s+the\s+grid|grid\s+draw)\b",
    re.IGNORECASE,
)
_CAP_RE = re.compile(
    r"\b(?:(?:must\s+|can|may\s+|should\s+)?not\s+exceed|cannot\s+exceed|"
    r"(?:no|not)\s+more\s+than|more\s+than|at\s+or\s+below|at\s+most|below|under|"
    r"cap(?:s|ped|ping)?|cap\s+of|limit(?:ed)?\s*(?:to|of)?|ceiling|maximum|max\.?|upper\s+limit|"
    r"restricted\s+to|constrained\s+to|derated\s+to|"
    r"(?:not|never)\s+(?:go\s+|rise\s+|climb\s+|get\s+)?(?:above|over|beyond)|"
    r"keep\s+.{0,25}?(?:under|below|to)|stay\s+(?:at\s+or\s+)?below|"
    r"hold\s+.{0,25}?(?:below|under|to))\b",
    re.IGNORECASE,
)

_SOLAR_WORD_RE = re.compile(
    r"\b(?:solar|pv|photovoltaic|panels?|modules?|strings?|rooftop|array|irradiance|sun\w*|yield|"
    r"generation|inverter\s+output|renewable\s+output)\b",
    re.IGNORECASE,
)


_SOC_PHRASE = re.compile(
    r"\bstate\s+of\s+charge\b|\bcharge\s+(?:level|state)\b", re.IGNORECASE
)


def classify(note: str) -> str:
    """Pick a directive type from the closed set. Order matters."""
    text = " ".join(str(note).split())
    # "state of charge" names a level, not the act of charging: reading it as a
    # charging prohibition turns a reserve floor into the wrong hard constraint.
    verbs = _SOC_PHRASE.sub("soc", text)

    # Discharge before charge: "discharge" contains "charge".
    if _NO_DISCHARGE_RE.search(verbs):
        return "no_discharge_window"
    if _NO_CHARGE_RE.search(verbs):
        return "no_charge_window"
    if _GRID_WORD_RE.search(text) and _CAP_RE.search(text) and _KWH_RE.search(text):
        return "max_grid_window"
    if _RESERVE_RE.search(text) and _BATTERY_WORD_RE.search(text):
        return "minimum_battery_reserve"
    if _SOLAR_WORD_RE.search(text) and (
        parse_factor(text) is not None
        or _TOTAL_LOSS_RE.search(text)
        or _HALVED_RE.search(text)
    ):
        return "solar_reduction"
    return "no_op"


# --------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------

_NOTE = "Deterministic fallback interpreter (no LLM provider reachable): "


def rule_based_interpret(notes: List[str], battery: BatteryInput) -> List[Dict[str, Any]]:
    """Same output shape as `interpreter.interpret_notes`, from regex heuristics.

    Minimum viable coverage:
      * time windows: "from 2 AM until 5 AM", "between 13:00 and 15:00",
        "1-3 PM", "noon", "midnight";
      * percentages: "80% reduction" -> 0.2, "drop to 25%" -> 0.25, "half",
        "one-fifth", "a quarter";
      * verbs: charge/charging -> no_charge_window, discharge -> no_discharge,
        "keep at least N kWh" -> reserve, "must not exceed N kWh" -> grid cap;
      * anything unmatched -> no_op.
    """
    return [
        _interpret_one(note, index, battery) for index, note in enumerate(notes)
    ]


def _interpret_one(note: str, index: int, battery: BatteryInput) -> Dict[str, Any]:
    text = " ".join(str(note).split())
    entry: Dict[str, Any] = {
        "note_index": index,
        "directive_type": "no_op",
        "hours": None,
        "factor": None,
        "minimum_energy_kwh": None,
        "max_grid_kwh": None,
        "explanation": _NOTE + "no energy directive was recognised in this note.",
        "source": "fallback",
    }

    directive_type = classify(text)
    if directive_type == "no_op":
        return entry

    hours = parse_hours(text)
    if hours is None:
        # A real directive with no readable window covers the whole horizon;
        # guessing a narrower one would silently under-apply it.
        hours = list(range(24))
        window_note = "no window was readable, so it is applied to all 24 hours"
    else:
        window_note = f"hours {hours}"

    entry["directive_type"] = directive_type
    entry["hours"] = hours

    if directive_type == "solar_reduction":
        factor = parse_factor(text)
        if factor is None:
            entry.update(
                directive_type="no_op",
                hours=None,
                explanation=_NOTE + "solar was mentioned without a readable amount.",
            )
            return entry
        entry["factor"] = factor
        entry["explanation"] = (
            f"{_NOTE}solar reduced to {factor:g} of forecast, {window_note}."
        )
        return entry

    if directive_type == "minimum_battery_reserve":
        reserve = parse_reserve(text, battery.capacity_kwh)
        if reserve is None:
            entry.update(
                directive_type="no_op",
                hours=None,
                explanation=_NOTE + "a reserve was implied without a readable level.",
            )
            return entry
        entry["minimum_energy_kwh"] = reserve
        entry["explanation"] = (
            f"{_NOTE}battery held at or above {reserve:g} kWh, {window_note}."
        )
        return entry

    if directive_type == "max_grid_window":
        cap = parse_grid_cap(text)
        if cap is None:
            entry.update(
                directive_type="no_op",
                hours=None,
                explanation=_NOTE + "a grid limit was implied without a readable cap.",
            )
            return entry
        entry["max_grid_kwh"] = cap
        entry["explanation"] = (
            f"{_NOTE}grid import capped at {cap:g} kWh per hour, {window_note}."
        )
        return entry

    verb = "charge" if directive_type == "no_charge_window" else "discharge"
    entry["explanation"] = f"{_NOTE}battery may not {verb}, {window_note}."
    return entry
