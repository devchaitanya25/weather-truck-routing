"""Weather risk classification.

Thresholds come straight from the assessment's risk table. The table's ranges
share edge values (e.g. rain 0.25 appears in both Moderate and High), so we
resolve them like this:

* Wind (mph):      <25 Low | 25-<35 Moderate | 35-<45 High | 45-<55 Severe | >=55 No Travel
* Rain (in/hr):    <0.10 Low | 0.10-0.25 Moderate | >0.25-0.50 High | >0.50-1.00 Severe | >1.00 No Travel
* Snow (in/hr):    <0.5 Low | 0.5-1.0 Moderate | >1.0-2.0 High | >2.0-3.0 Severe | >3.0 No Travel

Load rules then escalate the wind level:

* wind >= 55                      -> No Travel (any load)
* 45 <= wind < 55 and load > 30k  -> No Travel
* 35 <= wind < 45 and load > 40k  -> at least Severe
"""
from dataclasses import dataclass

LOW, MODERATE, HIGH, SEVERE, NO_TRAVEL = range(5)
LEVEL_NAMES = ["Low", "Moderate", "High", "Severe", "No Travel"]

HEAVY_LOAD_LB = 30_000
VERY_HEAVY_LOAD_LB = 40_000


def wind_level(mph: float) -> int:
    if mph >= 55:
        return NO_TRAVEL
    if mph >= 45:
        return SEVERE
    if mph >= 35:
        return HIGH
    if mph >= 25:
        return MODERATE
    return LOW


def rain_level(in_per_hr: float) -> int:
    if in_per_hr > 1.00:
        return NO_TRAVEL
    if in_per_hr > 0.50:
        return SEVERE
    if in_per_hr > 0.25:
        return HIGH
    if in_per_hr >= 0.10:
        return MODERATE
    return LOW


def snow_level(in_per_hr: float) -> int:
    if in_per_hr > 3.0:
        return NO_TRAVEL
    if in_per_hr > 2.0:
        return SEVERE
    if in_per_hr > 1.0:
        return HIGH
    if in_per_hr >= 0.5:
        return MODERATE
    return LOW


@dataclass(frozen=True)
class Assessment:
    level: int
    wind_level: int
    rain_level: int
    snow_level: int
    driver: str  # "wind" | "rain" | "snow" | "none"
    reason: str
    load_rule: str | None = None

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES[self.level]


def classify(wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lb: float) -> Assessment:
    """Classify conditions at one checkpoint for a given load weight."""
    w = wind_level(wind_mph)
    r = rain_level(rain_in_hr)
    s = snow_level(snow_in_hr)

    load_rule = None
    if 45 <= wind_mph < 55 and load_lb > HEAVY_LOAD_LB:
        w = NO_TRAVEL
        load_rule = "Wind 45-54 mph with a load over 30,000 lb"
    elif 35 <= wind_mph < 45 and load_lb > VERY_HEAVY_LOAD_LB:
        w = max(w, SEVERE)
        load_rule = "Wind 35-44 mph with a load over 40,000 lb"

    level = max(w, r, s)
    if level == LOW:
        return Assessment(level, w, r, s, "none", "Conditions favorable")

    # The factor that sets the overall level (ties resolved wind > rain > snow).
    driver = next(name for name, lvl in (("wind", w), ("rain", r), ("snow", s)) if lvl == level)
    detail = {
        "wind": f"Wind {wind_mph:.0f} mph",
        "rain": f"Rain {rain_in_hr:.2f} in/hr",
        "snow": f"Snow {snow_in_hr:.2f} in/hr",
    }[driver]
    reason = f"{detail} ({LEVEL_NAMES[level]})"
    if driver == "wind" and load_rule:
        reason += f" - {load_rule}"
    return Assessment(level, w, r, s, driver, reason, load_rule)
