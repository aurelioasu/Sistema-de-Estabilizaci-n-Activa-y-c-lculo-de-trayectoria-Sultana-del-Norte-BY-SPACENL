from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FlightComparison:
    simulated: dict[str, float]
    measured: dict[str, float]
    error: dict[str, float]


def _metrics(samples: Any) -> dict[str, float]:
    max_altitude = max(float(sample.altitude_agl_m) for sample in samples)
    apogee = next(sample for sample in samples if float(sample.altitude_agl_m) == max_altitude)
    last = samples[-1]
    rail_exit = next((sample for sample in samples if not sample.on_rail), None)
    rail_exit_speed = float(getattr(rail_exit, "airspeed_mps", 0.0)) if rail_exit is not None else 0.0
    if rail_exit is not None and hasattr(rail_exit, "velocity_enu_mps"):
        rail_exit_speed = float(rail_exit.velocity_enu_mps.dot(rail_exit.velocity_enu_mps) ** 0.5)
    return {
        "apogee_agl_m": max_altitude,
        "time_to_apogee_s": float(apogee.time_s),
        "max_speed_mps": max(float(sample.airspeed_mps) for sample in samples),
        "rail_exit_speed_mps": rail_exit_speed,
        "landing_east_m": float(last.position_enu_m[0]), "landing_north_m": float(last.position_enu_m[1]),
    }


def compare_result_to_telemetry(result: Any, telemetry_csv: str | Path) -> FlightComparison:
    """Compare a simulation with an exported flight log; original log rows are never mutated."""
    path = Path(telemetry_csv)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Telemetry CSV is empty")
    aliases = {"apogee_agl_m": "altitude_agl_m", "time_to_apogee_s": "time_s", "max_speed_mps": "airspeed_mps", "landing_east_m": "position_enu_m_0", "landing_north_m": "position_enu_m_1"}
    measured: dict[str, float] = {}
    for metric, column in aliases.items():
        if column not in rows[0]:
            continue
        values = [float(row[column]) for row in rows if row.get(column) not in (None, "")]
        if metric in {"apogee_agl_m", "max_speed_mps"}:
            measured[metric] = max(values)
        elif metric == "time_to_apogee_s" and "altitude_agl_m" in rows[0]:
            apogee_row = max(rows, key=lambda row: float(row.get("altitude_agl_m") or "-inf"))
            measured[metric] = float(apogee_row[column])
        else:
            measured[metric] = values[-1]
    simulated = _metrics(result.samples)
    error = {key: simulated[key] - value for key, value in measured.items() if key in simulated}
    return FlightComparison(simulated, measured, error)
