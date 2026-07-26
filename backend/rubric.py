"""Loading and flattening rubric/rubric.json.

The rubric is general to both frameworks' principles. Nothing here — and nothing
in the prompts built from it — is specific to any one design or client.
"""

from __future__ import annotations

import functools
import json
import os
import pathlib
from dataclasses import dataclass

def _rubric_path() -> pathlib.Path:
    """Locate rubric.json in both layouts.

    Local dev runs from the repo, where the rubric lives at `rubric/rubric.json`.
    The Lambda build (see backend/Makefile) copies it to the package root, since
    the deployment artifact is flattened and has no parent directory.
    """
    override = os.environ.get("RUBRIC_PATH")
    if override:
        return pathlib.Path(override)

    here = pathlib.Path(__file__).resolve().parent
    for candidate in (here / "rubric.json", here.parent / "rubric" / "rubric.json"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("rubric.json not found; set RUBRIC_PATH")

# Severity weighting used when rolling checks up into a pillar score.
SEVERITY_WEIGHT: dict[str, float] = {"high": 3.0, "medium": 2.0, "low": 1.0}


@dataclass(frozen=True)
class Check:
    framework: str
    pillar_id: str
    pillar_name: str
    check_id: str
    description: str
    severity: str


@dataclass(frozen=True)
class Pillar:
    framework: str
    pillar_id: str
    name: str
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class Framework:
    key: str
    name: str
    pillars: tuple[Pillar, ...]


@functools.lru_cache(maxsize=1)
def load() -> tuple[Framework, ...]:
    """Parse rubric.json into an immutable, cacheable structure."""
    raw = json.loads(_rubric_path().read_text())
    frameworks: list[Framework] = []
    for key, fw in raw.get("frameworks", {}).items():
        pillars: list[Pillar] = []
        for p in fw.get("pillars", []):
            checks = tuple(
                Check(
                    framework=key,
                    pillar_id=p["id"],
                    pillar_name=p["name"],
                    check_id=c["id"],
                    description=c["description"],
                    severity=c.get("severity_default", "medium"),
                )
                for c in p.get("checks", [])
            )
            pillars.append(
                Pillar(framework=key, pillar_id=p["id"], name=p["name"], checks=checks)
            )
        frameworks.append(Framework(key=key, name=fw.get("name", key), pillars=tuple(pillars)))
    return tuple(frameworks)


@functools.lru_cache(maxsize=1)
def all_checks() -> tuple[Check, ...]:
    return tuple(c for fw in load() for p in fw.pillars for c in p.checks)


@functools.lru_cache(maxsize=1)
def checks_by_id() -> dict[str, Check]:
    return {c.check_id: c for c in all_checks()}


@functools.lru_cache(maxsize=1)
def as_prompt_block() -> str:
    """Render the rubric for the evaluate stage.

    Stable across every request, so it sits at the front of the prompt behind a
    cache breakpoint — see agent/stages.py.
    """
    lines: list[str] = []
    for fw in load():
        lines.append(f"\n## Framework: {fw.name} (key: {fw.key})")
        for p in fw.pillars:
            lines.append(f"\n### Pillar: {p.name} (id: {p.pillar_id})")
            for c in p.checks:
                lines.append(f"- [{c.check_id}] ({c.severity}) {c.description}")
    return "\n".join(lines).strip()
