"""Route ranking.

Preference order (from the brief): fewest Severe miles, fewest High miles,
lowest average risk, shortest travel time.

Two deliberate additions:
* Routes that cross No Travel conditions are not drivable, so fewest No Travel
  miles is compared first.
* Comparisons use small tolerance buckets (nearest mile, 0.1 of average risk) so
  a 0.2-mile difference in Severe exposure doesn't outrank a much faster route.
"""
from __future__ import annotations


def sort_key(route: dict) -> tuple:
    m = route["miles_by_level"]
    return (
        round(m["No Travel"]),
        round(m["Severe"]),
        round(m["High"]),
        round(route["avg_risk"], 1),
        route["duration_s"],
    )


_CRITERIA = ["No Travel miles", "Severe miles", "High miles", "average risk", "travel time"]


def _fmt(idx: int, route: dict) -> str:
    m = route["miles_by_level"]
    if idx == 0:
        return f"{m['No Travel']:.0f} mi"
    if idx == 1:
        return f"{m['Severe']:.0f} mi"
    if idx == 2:
        return f"{m['High']:.0f} mi"
    if idx == 3:
        return f"{route['avg_risk']:.2f}"
    h, rem = divmod(int(route["duration_s"]), 3600)
    return f"{h}h {rem // 60:02d}m"


def rank_routes(routes: list[dict]) -> list[dict]:
    """Return routes ordered best-first, annotating rank / recommended / drivable."""
    ranked = sorted(routes, key=sort_key)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
        r["recommended"] = i == 0
    return ranked


def explain(ranked: list[dict]) -> dict:
    best = ranked[0]
    reasons: list[str] = []
    for other in ranked[1:]:
        kb, ko = sort_key(best), sort_key(other)
        idx = next((i for i in range(len(kb)) if kb[i] != ko[i]), len(kb) - 1)
        if idx == 4:
            reasons.append(
                f"{other['label']} has the same weather exposure but takes longer "
                f"({_fmt(4, other)} vs {_fmt(4, best)})."
            )
        else:
            reasons.append(
                f"{other['label']} has more {_CRITERIA[idx]}: {_fmt(idx, other)} vs {_fmt(idx, best)}."
            )

    if best["miles_by_level"]["No Travel"] >= 0.5:
        summary = (
            "Every route crosses No Travel conditions at the planned departure time. "
            f"{best['label']} is the least bad option, but consider delaying departure."
        )
    else:
        summary = (
            f"{best['label']} is recommended: {best['miles_by_level']['Severe']:.0f} mi of Severe and "
            f"{best['miles_by_level']['High']:.0f} mi of High weather, average risk {best['avg_risk']:.2f}/4, "
            f"{_fmt(4, best)} drive."
        )
    return {"route_id": best["id"], "summary": summary, "reasons": reasons}
