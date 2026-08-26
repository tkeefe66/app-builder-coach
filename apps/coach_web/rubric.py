"""Grading rubric, read from the repo's rubric.yaml (single source)."""
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from . import taxonomy

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

TIER_NAMES = ("core", "standard", "specialty")


class RubricError(ValueError):
    """rubric.yaml is invalid; raised at startup, never at request time."""


@dataclass(frozen=True)
class Gate:
    min_count: int
    min_avg_complexity: float | None = None
    within_days: int | None = None


@dataclass(frozen=True)
class Level:
    name: str
    label: str
    breadth: int = 0
    gates: dict[str, Gate] = field(default_factory=dict)
    noncore: tuple[int, int] | None = None  # (distinct tags, min builds each)


@dataclass(frozen=True)
class Rubric:
    tiers: dict[str, str]            # tag -> core | standard | specialty
    levels: tuple[Level, ...]        # ordered, lowest first
    pairs_with: dict[str, list[str]]


def _parse(raw: dict) -> Rubric:
    known = set(taxonomy.all_tags())
    tiers: dict[str, str] = {}
    for tier in TIER_NAMES:
        for tag in (raw.get("tiers", {}).get(tier) or []):
            if tag not in known:
                raise RubricError(
                    f"rubric.yaml: unknown tag {tag!r} in tiers.{tier}")
            if tag in tiers:
                raise RubricError(
                    f"rubric.yaml: tag {tag!r} appears in two tiers")
            tiers[tag] = tier
    missing = known - set(tiers)
    if missing:
        raise RubricError(
            f"rubric.yaml: taxonomy tags missing a tier: {sorted(missing)}")

    raw_levels = raw.get("levels") or []
    if not raw_levels:
        raise RubricError("rubric.yaml: levels must be a non-empty list")
    levels: list[Level] = []
    for lv in raw_levels:
        if "name" not in lv:
            raise RubricError("rubric.yaml: level missing key 'name'")
        level_name = lv["name"]
        gates: dict[str, Gate] = {}
        for tag, g in (lv.get("gates") or {}).items():
            if tag not in known:
                raise RubricError(
                    f"rubric.yaml: unknown tag {tag!r} in level "
                    f"{level_name!r} gates")
            if "min_count" not in g:
                raise RubricError(
                    f"rubric.yaml: level {level_name!r} gate {tag!r} "
                    f"missing key 'min_count'")
            gates[tag] = Gate(
                min_count=int(g["min_count"]),
                min_avg_complexity=(float(g["min_avg_complexity"])
                                    if "min_avg_complexity" in g else None),
                within_days=(int(g["within_days"])
                             if "within_days" in g else None))
        noncore = None
        if "noncore" in lv:
            nc = lv["noncore"]
            for key in ("tags", "min_count"):
                if key not in nc:
                    raise RubricError(
                        f"rubric.yaml: level {level_name!r} noncore "
                        f"missing key {key!r}")
            noncore = (int(nc["tags"]), int(nc["min_count"]))
        if "label" not in lv:
            raise RubricError(
                f"rubric.yaml: level {level_name!r} missing key 'label'")
        levels.append(Level(name=lv["name"], label=lv["label"],
                            breadth=int(lv.get("breadth", 0)),
                            gates=gates, noncore=noncore))

    for prev, nxt in zip(levels, levels[1:]):
        for tag in set(prev.gates) & set(nxt.gates):
            pg, ng = prev.gates[tag], nxt.gates[tag]
            if ng.min_count < pg.min_count:
                raise RubricError(
                    f"rubric.yaml: level {nxt.name!r} gate {tag!r} "
                    f"min_count ({ng.min_count}) is lower than level "
                    f"{prev.name!r} ({pg.min_count})")
            if (pg.min_avg_complexity is not None
                    and ng.min_avg_complexity is not None
                    and ng.min_avg_complexity < pg.min_avg_complexity):
                raise RubricError(
                    f"rubric.yaml: level {nxt.name!r} gate {tag!r} "
                    f"min_avg_complexity ({ng.min_avg_complexity}) is lower "
                    f"than level {prev.name!r} ({pg.min_avg_complexity})")

    pairs: dict[str, list[str]] = {}
    for tag, related in (raw.get("pairs_with") or {}).items():
        bad = [t for t in [tag, *(related or [])] if t not in known]
        if bad:
            raise RubricError(
                f"rubric.yaml: unknown tag(s) in pairs_with: {bad}")
        pairs[tag] = list(related or [])

    return Rubric(tiers=tiers, levels=tuple(levels), pairs_with=pairs)


@lru_cache(maxsize=1)
def load() -> Rubric:
    return _parse(yaml.safe_load((REPO_ROOT / "rubric.yaml").read_text()))
