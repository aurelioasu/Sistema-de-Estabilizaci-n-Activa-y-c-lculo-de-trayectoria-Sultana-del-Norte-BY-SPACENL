"""Persistence for laboratory scenarios without changing the project defaults."""
from __future__ import annotations

import copy
import csv
import re
from pathlib import Path
from typing import Any

import yaml

from .config_loader import ConfigValidationError, LoadedScenario, load_scenario


def project_root(path: Path) -> Path:
    """Find the repository root for both built-in and saved scenarios."""
    for candidate in (path.parent, *path.parents):
        if (candidate / "configs").is_dir() and (candidate / "data").is_dir():
            return candidate
    raise ConfigValidationError(f"Cannot locate the project root from {path}")


def scenario_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ConfigValidationError("El escenario necesita un nombre")
    return slug[:48]


def _write_csv(path: Path, columns: list[str], rows: list[list[float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def save_laboratory_scenario(root: Path, name: str, vehicle: dict[str, Any], environment: dict[str, Any],
                             thrust_rows: list[list[float]], mass_rows: list[list[float]], aero_rows: list[list[float]]) -> LoadedScenario:
    """Save a complete, self-contained scenario under configs/scenarios/<name>."""
    slug = scenario_slug(name)
    target = root / "configs" / "scenarios" / slug
    data = target / "data"
    data.mkdir(parents=True, exist_ok=True)
    _write_csv(data / "thrust_curve.csv", ["time_s", "thrust_n"], thrust_rows)
    _write_csv(data / "mass_profile.csv", ["time_s", "propellant_mass_kg", "cg_m", "ixx_kg_m2", "iyy_kg_m2", "izz_kg_m2"], mass_rows)
    _write_csv(data / "aero_table.csv", ["mach", "cd", "cn_alpha_per_rad"], aero_rows)
    saved_vehicle, saved_environment = copy.deepcopy(vehicle), copy.deepcopy(environment)
    saved_vehicle["vehicle_id"] = f"{saved_vehicle.get('vehicle_id', 'sultana')}-{slug}"
    # A saved table must be independent from the preliminary source tables.
    saved_vehicle["propulsion"]["thrust_curve_csv"] = f"configs/scenarios/{slug}/data/thrust_curve.csv"
    saved_vehicle["mass"]["mass_curve_csv"] = f"configs/scenarios/{slug}/data/mass_profile.csv"
    saved_vehicle["aerodynamics"]["aero_table_csv"] = f"configs/scenarios/{slug}/data/aero_table.csv"
    vehicle_path, environment_path = target / "vehicle.yaml", target / "environment.yaml"
    with vehicle_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(saved_vehicle, handle, allow_unicode=True, sort_keys=False)
    with environment_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(saved_environment, handle, allow_unicode=True, sort_keys=False)
    return load_scenario(vehicle_path, environment_path)
