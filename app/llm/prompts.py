"""
Prompt assets for operator-note interpretation.  OWNER: Ninad — Lane B.

The prompt is the single highest-leverage artifact in this repo: 25 of the 100
points are scored directly on what comes out of it, and another 25 depend on it
being right. Treat it like code — version it, test it against
`harness/paraphrase_bank.json`, never tune it against a single note.

Hard rules the prompt must encode (Problem Statement §04, §05, §08):
  * Exactly one entry per note, in note_index order.
  * directive_type from the closed set only; unrelated notes -> "no_op".
  * Windows are start-inclusive / end-exclusive: "1 PM to 3 PM" -> [13, 14].
  * hours: unique ints 0..23, ascending (a window that wraps midnight still
    sorts ascending, e.g. 10 PM-2 AM -> [0, 1, 22, 23]).
  * solar_reduction.factor is the fraction that REMAINS: "80% reduction" -> 0.2,
    "drops to 20%" -> 0.2, "about half" -> 0.5, "one-fifth" -> 0.2.
  * minimum_battery_reserve given as a percentage is a percentage of
    battery.capacity_kwh -> the prompt is given capacity so it can do the math.
  * Never invent demand, solar, tariff or battery values.

None of the few-shot examples below are copied from `tests/data/public_samples.json`
or from the Problem Statement's worked examples. The hidden notes are paraphrases
we have never seen; teaching the model the public wordings teaches it to match
phrases instead of meaning, which is exactly what the Guide penalises.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.schemas import BatteryInput

# The closed set. Kept as a literal here (rather than imported from schemas) so
# that the prompt text and the JSON schema handed to the provider can never
# drift apart from each other.
_TYPES: tuple[str, ...] = (
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
)


SYSTEM_PROMPT = """\
You convert short campus-operator notes into structured directives for a 24-hour
electricity scheduler. You are an extraction engine, not a chat assistant: you
emit JSON and nothing else: no prose, no markdown fences, no commentary.

OUTPUT SHAPE
Return exactly one JSON object:
{"interpretations": [ <one object per note, note_index ascending from 0> ]}

Each object has all seven keys, always:
{"note_index": <int>, "directive_type": <one of the six below>,
 "hours": <array of int or null>, "factor": <number or null>,
 "minimum_energy_kwh": <number or null>, "max_grid_kwh": <number or null>,
 "explanation": <one short sentence>}
Keys the chosen directive_type does not use are null. Never add other keys.

THE SIX DIRECTIVE TYPES (no other value is legal)
  solar_reduction          usable solar is reduced during some hours.
                           -> hours + factor
  minimum_battery_reserve  stored battery energy must stay at or above a level.
                           -> hours + minimum_energy_kwh
  no_charge_window         the battery cannot be charged during some hours.
                           -> hours
  no_discharge_window      the battery cannot be discharged during some hours.
                           -> hours
  max_grid_window          grid import is capped during some hours.
                           -> hours + max_grid_kwh
  no_op                    the note does not change today's 24-hour electricity
                           schedule. -> hours, factor, minimum_energy_kwh and
                           max_grid_kwh are ALL null

RULE 1. WINDOWS ARE START-INCLUSIVE AND END-EXCLUSIVE.
The end hour is NOT part of the window. This is the most common mistake; check
every window twice.
  "from 1 PM to 3 PM"        -> [13, 14]            (2 hours, not 3)
  "from 6 PM until 10 PM"    -> [18, 19, 20, 21]    (4 hours, not 5)
  "between 11 AM and 2 PM"   -> [11, 12, 13]        (3 hours, not 4)
  "09:00-12:00"              -> [9, 10, 11]         (3 hours, not 4)
  "from 10 PM to 2 AM"       -> [0, 1, 22, 23]      (wraps midnight)
A window given as a duration counts forward from the start hour:
  "for three hours from 10 AM"                      -> [10, 11, 12]
A single named hour is that one hour: "during the 3 PM hour" -> [15].
"noon" is 12, "midnight" is 0, "1700" and "17:00" are 17.

RULE 2. hours is always unique whole integers 0..23 sorted ASCENDING.
A window that wraps past midnight still sorts ascending: 11 PM to 2 AM is
[0, 1, 23], never [23, 0, 1]. Never write 24; hour 24 does not exist.

RULE 3. factor is the fraction of solar that REMAINS, between 0 and 1.
  "an 80% reduction"          -> 0.2      (80% is lost, 20% remains)
  "output drops to 20%"       -> 0.2      (20% remains)
  "reduced by 60%"            -> 0.4
  "down to about a quarter"   -> 0.25
  "roughly half the forecast" -> 0.5
  "one-fifth of normal"       -> 0.2
  "cut by three quarters"     -> 0.25
Read the preposition: "by N%" or "N% reduction/loss/drop" means 1 - N/100
remains; "to N%" or "N% of" means N/100 remains. Solar that is fully lost
("no usable solar", "panels offline") is factor 0.

RULE 4. minimum_energy_kwh is an absolute kWh number.
If the note states a percentage or fraction of the battery instead, convert it
using the capacity_kwh given in the user message:
  capacity 400, "keep at least 25% of capacity"  -> 100
  capacity 400, "hold half the pack in reserve"  -> 200
If the note states kWh directly, use that number unchanged.

RULE 5. max_grid_kwh is the per-hour cap on grid import, in kWh, exactly as
stated. Notes about the feeder, transformer, substation, grid intake, grid draw
or imported power capping at a number are max_grid_window.

RULE 6. no_op is for notes that do not change the electricity schedule.
Campus life is full of deliberate distractors: menus, room bookings, library or
office hours, registration and exam deadlines, notices, emails, cleaning
rotas, sports fixtures, wifi and printer problems, staff leave. Mark them no_op
with every value field null. Do not stretch a note into an energy directive
because it mentions a time. But a note IS a directive whenever it changes solar
availability, battery charging, battery discharging, stored-energy floor, or
grid import, however casually it is phrased.

RULE 7. Never invent numbers. Do not guess demand, tariff, solar kWh, battery
rate limits, or a cap that the note does not state. Use only the note's own
numbers and the capacity given to you. If a real directive names no hours at
all and plainly covers the whole day, use all 24 hours [0..23].

RULE 8. One object per note, in the order the notes were given, note_index
matching the bracketed index in the user message. Never merge two notes into one
object, never split one note into two, never drop a note. If one note contains
two different directives, return the one that constrains the schedule most
directly and say so in the explanation.

RULE 9. explanation is one short factual sentence about what was extracted. It
is read by humans, not matched by machine. Do not apologise, do not hedge, do
not restate these rules.
"""


# 4-8 few-shots: one per directive type, both "reduction"/"drops to" phrasings
# of factor, both kWh and percentage reserves, and a distractor. Deliberately
# written in wordings that do NOT appear in the public sample pack.
FEW_SHOT: List[dict] = [
    {
        "user": (
            "BATTERY: capacity_kwh=300, base minimum_energy_kwh=60\n"
            "NOTES (1):\n"
            "[0] Inverter firmware work will cut PV output by 60% from 09:00 to 12:00.\n"
            "Return one object per note, note_index 0..0."
        ),
        "assistant": (
            '{"interpretations": [{"note_index": 0, "directive_type": "solar_reduction", '
            '"hours": [9, 10, 11], "factor": 0.4, "minimum_energy_kwh": null, '
            '"max_grid_kwh": null, "explanation": "PV output is cut by 60% for three '
            'hours from 09:00, so 40% of the forecast remains."}]}'
        ),
    },
    {
        "user": (
            "BATTERY: capacity_kwh=300, base minimum_energy_kwh=60\n"
            "NOTES (2):\n"
            "[0] Heavy overcast is forecast; usable generation falls to about a third of "
            "normal from 10 AM until 1 PM.\n"
            "[1] The print shop is closed for stocktaking on Thursday.\n"
            "Return one object per note, note_index 0..1."
        ),
        "assistant": (
            '{"interpretations": [{"note_index": 0, "directive_type": "solar_reduction", '
            '"hours": [10, 11, 12], "factor": 0.33, "minimum_energy_kwh": null, '
            '"max_grid_kwh": null, "explanation": "Cloud cover leaves about one third of '
            'forecast solar from 10:00 through 12:00."}, '
            '{"note_index": 1, "directive_type": "no_op", "hours": null, "factor": null, '
            '"minimum_energy_kwh": null, "max_grid_kwh": null, "explanation": "A shop '
            'closure does not affect the 24-hour energy schedule."}]}'
        ),
    },
    {
        "user": (
            "BATTERY: capacity_kwh=400, base minimum_energy_kwh=80\n"
            "NOTES (1):\n"
            "[0] The clinic needs a quarter of the pack still stored between 8 PM and 11 PM.\n"
            "Return one object per note, note_index 0..0."
        ),
        "assistant": (
            '{"interpretations": [{"note_index": 0, "directive_type": '
            '"minimum_battery_reserve", "hours": [20, 21, 22], "factor": null, '
            '"minimum_energy_kwh": 100, "max_grid_kwh": null, "explanation": "A quarter '
            'of the 400 kWh capacity is 100 kWh, held from 20:00 through 22:00."}]}'
        ),
    },
    {
        "user": (
            "BATTERY: capacity_kwh=250, base minimum_energy_kwh=50\n"
            "NOTES (1):\n"
            "[0] Do not let stored energy fall under 140 kWh for the three hours starting "
            "at 5 PM.\n"
            "Return one object per note, note_index 0..0."
        ),
        "assistant": (
            '{"interpretations": [{"note_index": 0, "directive_type": '
            '"minimum_battery_reserve", "hours": [17, 18, 19], "factor": null, '
            '"minimum_energy_kwh": 140, "max_grid_kwh": null, "explanation": "Stored '
            'energy stays at or above 140 kWh for the three hours from 17:00."}]}'
        ),
    },
    {
        "user": (
            "BATTERY: capacity_kwh=250, base minimum_energy_kwh=50\n"
            "NOTES (1):\n"
            "[0] Rectifier swap means the pack takes no energy in from 03:00 to 06:00.\n"
            "Return one object per note, note_index 0..0."
        ),
        "assistant": (
            '{"interpretations": [{"note_index": 0, "directive_type": "no_charge_window", '
            '"hours": [3, 4, 5], "factor": null, "minimum_energy_kwh": null, '
            '"max_grid_kwh": null, "explanation": "The battery cannot take charge during '
            'the rectifier swap from 03:00 through 05:00."}]}'
        ),
    },
    {
        "user": (
            "BATTERY: capacity_kwh=500, base minimum_energy_kwh=100\n"
            "NOTES (2):\n"
            "[0] Relay coordination checks: the pack must not supply any load from 4 PM "
            "to 7 PM.\n"
            "[1] Please remind staff that the shuttle timetable changes on Sunday.\n"
            "Return one object per note, note_index 0..1."
        ),
        "assistant": (
            '{"interpretations": [{"note_index": 0, "directive_type": '
            '"no_discharge_window", "hours": [16, 17, 18], "factor": null, '
            '"minimum_energy_kwh": null, "max_grid_kwh": null, "explanation": "The '
            'battery may not discharge from 16:00 through 18:00 during relay checks."}, '
            '{"note_index": 1, "directive_type": "no_op", "hours": null, "factor": null, '
            '"minimum_energy_kwh": null, "max_grid_kwh": null, "explanation": "A shuttle '
            'timetable change has no effect on the energy schedule."}]}'
        ),
    },
    {
        "user": (
            "BATTERY: capacity_kwh=500, base minimum_energy_kwh=100\n"
            "NOTES (1):\n"
            "[0] Utility has derated our incomer: keep imported power at or under 210 kWh "
            "each hour between 8 PM and midnight.\n"
            "Return one object per note, note_index 0..0."
        ),
        "assistant": (
            '{"interpretations": [{"note_index": 0, "directive_type": "max_grid_window", '
            '"hours": [20, 21, 22, 23], "factor": null, "minimum_energy_kwh": null, '
            '"max_grid_kwh": 210, "explanation": "Grid import is capped at 210 kWh per '
            'hour from 20:00 through 23:00."}]}'
        ),
    },
    {
        "user": (
            "BATTERY: capacity_kwh=200, base minimum_energy_kwh=40\n"
            "NOTES (2):\n"
            "[0] Charging is locked out overnight from 11 PM through to 2 AM while the "
            "cabinet is rewired.\n"
            "[1] Exam results will be published on the noticeboard at 3 PM.\n"
            "Return one object per note, note_index 0..1."
        ),
        "assistant": (
            '{"interpretations": [{"note_index": 0, "directive_type": "no_charge_window", '
            '"hours": [0, 1, 23], "factor": null, "minimum_energy_kwh": null, '
            '"max_grid_kwh": null, "explanation": "Charging is unavailable for the '
            'wrap-around window 23:00, 00:00 and 01:00."}, '
            '{"note_index": 1, "directive_type": "no_op", "hours": null, "factor": null, '
            '"minimum_energy_kwh": null, "max_grid_kwh": null, "explanation": "Publishing '
            'exam results does not change the energy schedule."}]}'
        ),
    },
]


def build_user_prompt(notes: List[str], battery: BatteryInput) -> str:
    """Render the per-request user message.

    Must include the note list with explicit zero-based indices and the battery
    capacity (needed to resolve "50% of capacity" style reserves).
    """
    n = len(notes)
    lines = [
        f"BATTERY: capacity_kwh={_num(battery.capacity_kwh)}, "
        f"base minimum_energy_kwh={_num(battery.minimum_energy_kwh)}",
        f"NOTES ({n}):",
    ]
    for i, note in enumerate(notes):
        lines.append(f"[{i}] {' '.join(str(note).split())}")
    last = n - 1
    lines.append(
        f"Return one object per note, note_index 0..{last}."
        if n > 1
        else "Return one object per note, note_index 0..0."
    )
    return "\n".join(lines)


def _num(value: float) -> str:
    """Render a battery number without a pointless trailing '.0'."""
    return str(int(value)) if float(value).is_integer() else str(value)


def response_json_schema() -> Dict[str, Any]:
    """JSON schema handed to the provider for structured / constrained output.

    Shape:
      {"interpretations": [
         {"note_index": int, "directive_type": <enum>,
          "hours": [int] | null, "factor": number | null,
          "minimum_energy_kwh": number | null, "max_grid_kwh": number | null,
          "explanation": str}
      ]}

    Deliberately FLAT: one object shape for all directive types, with unused
    fields null. Flat schemas survive weaker models far better than a oneOf
    union, and app.guardrails re-assembles the exact structured_adjustment.
    """
    entry = {
        "type": "object",
        "properties": {
            "note_index": {
                "type": "integer",
                "description": "Zero-based index of the note this object interprets.",
            },
            "directive_type": {"type": "string", "enum": list(_TYPES)},
            "hours": {
                "type": ["array", "null"],
                "items": {"type": "integer"},
                "description": (
                    "Unique whole hours 0-23, ascending, start-inclusive and "
                    "end-exclusive. Null only for no_op."
                ),
            },
            "factor": {
                "type": ["number", "null"],
                "description": "solar_reduction only: fraction of solar remaining, 0-1.",
            },
            "minimum_energy_kwh": {
                "type": ["number", "null"],
                "description": "minimum_battery_reserve only: absolute kWh floor.",
            },
            "max_grid_kwh": {
                "type": ["number", "null"],
                "description": "max_grid_window only: per-hour grid import cap in kWh.",
            },
            "explanation": {"type": "string"},
        },
        # Every key required, unused ones explicitly null: this is what OpenAI
        # strict structured outputs demands, and it also stops small models
        # from quietly omitting a field.
        "required": [
            "note_index",
            "directive_type",
            "hours",
            "factor",
            "minimum_energy_kwh",
            "max_grid_kwh",
            "explanation",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"interpretations": {"type": "array", "items": entry}},
        "required": ["interpretations"],
        "additionalProperties": False,
    }
