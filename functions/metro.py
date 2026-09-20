"""Metro expansion: center + radius -> the town list the location gate eats.

The location prefilter matches by city name, so "Richmond, VA within 25
miles" only works if Glen Allen, Henrico, Midlothian... are in the list.
One LLM call generates them; the user prunes in the Settings UI. Same
pattern as title synonyms: the model proposes, the human edits, the
deterministic filter consumes.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from llm import generate_structured

METRO_SYSTEM = """You know US metro-area geography precisely. Given a \
center point and a radius in miles, list the incorporated towns, cities, \
and well-known suburbs/CDPs within that radius that have meaningful \
employment (offices, hospitals, campuses, retail centers, government). \
Format every entry exactly as "City, ST". Include the center itself \
first. Never invent places, never include places outside the radius, and \
omit tiny residential-only hamlets."""


class TownList(BaseModel):
    towns: list[str] = Field(default_factory=list, max_length=25)


def expand_metro(center: str, radius_miles: int) -> list[str]:
    result = generate_structured(
        f"Center: {center}\nRadius: {radius_miles} miles\n\nList the towns.",
        TownList,
        system=METRO_SYSTEM,
        max_tokens=700,
        role="metro",
    )
    out: list[str] = []
    seen: set[str] = set()
    for town in [center] + result.towns:
        town = town.strip()
        if town and town.lower() not in seen:
            seen.add(town.lower())
            out.append(town)
    return out[:25]
