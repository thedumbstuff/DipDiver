"""Registry API — what universes/configs exist on disk.

Surfaces:
  GET /api/available-universes  → [{key, label, size, region, live_executable}]
  GET /api/available-configs    → [{filename, universe, model_kind}]

Used by /config to populate validated dropdowns instead of free text.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from dipdiver._paths import repo_root


router = APIRouter()


def _configs_dir() -> Path:
    return repo_root() / "dipdiver" / "brain" / "baselines" / "configs"


@router.get("/api/available-configs")
async def available_configs() -> list[dict]:
    """Return all YAML configs found on disk, parsed minimally for the form."""
    out: list[dict] = []
    cfg_dir = _configs_dir()
    if not cfg_dir.exists():
        return out
    for path in sorted(cfg_dir.glob("*.yaml")):
        filename = path.name
        stem = path.stem  # e.g. "dow30_lightgbm"
        if "_" in stem:
            universe, _, model = stem.partition("_")
        else:
            universe, model = stem, ""
        out.append({
            "filename": filename,
            "stem": stem,
            "universe": universe,
            "model_kind": model,
        })
    return out


@router.get("/api/available-universes")
async def available_universes() -> list[dict]:
    """Return registered universes from the Universe registry.

    Falls back to deriving the universe list from config filenames when the
    Universe registry can't be imported (e.g. older codebases / minimal envs).
    """
    try:
        from dipdiver.brain.baselines.universes import UNIVERSES
    except Exception:  # noqa: BLE001
        # Fallback: derive from config filenames
        seen: set[str] = set()
        derived: list[dict] = []
        for entry in await available_configs():
            u = entry["universe"]
            if u in seen:
                continue
            seen.add(u)
            derived.append({
                "key": u, "label": u, "size": None, "region": None,
                "live_executable": None,
            })
        return derived

    out = []
    for key, u in sorted(UNIVERSES.items()):
        out.append({
            "key": key,
            "label": getattr(u, "label", key),
            "size": len(getattr(u, "symbols", [])),
            "region": getattr(u, "region", None),
            "live_executable": getattr(u, "live_executable", True),
        })
    return out
