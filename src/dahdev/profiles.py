from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Profile:
    id: str
    title: str
    status: str
    notes: str


PROFILES = (
    Profile(
        id="dah-prototype",
        title="Destroy All Humans! prototype",
        status="planned",
        notes="Requires build-pinned samples and a golden fixture.",
    ),
    Profile(
        id="dah1-retail-xbox-us",
        title="Destroy All Humans! retail (Xbox, USA)",
        status="fixture-verification",
        notes="Area 42 source and corrected terrain reference hashes are known.",
    ),
    Profile(
        id="dah2-retail",
        title="Destroy All Humans! 2 retail",
        status="planned",
        notes="Platform and build variants must be catalogued before decoding.",
    ),
)


def profiles_as_dicts() -> list[dict[str, str]]:
    return [asdict(profile) for profile in PROFILES]

