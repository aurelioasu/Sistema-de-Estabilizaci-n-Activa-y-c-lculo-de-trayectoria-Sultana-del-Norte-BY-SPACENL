from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _vector(value: Any) -> list[float]:
    return [float(item) for item in value]


def export_result(result: Any, output_directory: str | Path, manifest: dict[str, Any]) -> dict[str, Path]:
    """Persist transparent, tool-friendly artifacts; Parquet is optional."""
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path, manifest_path = directory / f"telemetry_{stamp}.csv", directory / f"manifest_{stamp}.json"
    rows = []
    for sample in result.samples:
        row = {
            "time_s": sample.time_s, "latitude_deg": sample.latitude_deg, "longitude_deg": sample.longitude_deg,
            "altitude_msl_m": sample.altitude_msl_m, "altitude_agl_m": sample.altitude_agl_m, "mach": sample.mach,
            "dynamic_pressure_pa": sample.dynamic_pressure_pa, "surface_temperature_k": sample.surface_temperature_k,
            "air_temperature_k": sample.air_temperature_k, "air_pressure_pa": sample.air_pressure_pa,
            "air_relative_humidity": sample.air_relative_humidity, "air_density_kg_m3": sample.air_density_kg_m3,
            "estimated_altitude_agl_m": sample.estimated_altitude_agl_m,
            "airspeed_mps": sample.airspeed_mps, "thrust_n": sample.thrust_n, "mass_kg": sample.mass_kg,
            "drag_force_n": sample.drag_force_n, "rain_impact_force_n": sample.rain_impact_force_n,
            "canard_lift_n": sample.canard_lift_n, "static_margin_calibers": sample.static_margin_calibers,
            "friction_heat_proxy": sample.friction_heat_proxy, "parachute_deployed": sample.parachute_deployed,
            "cg_m": sample.cg_m, "on_rail": sample.on_rail, "parachute_cds_m2": sample.parachute_cds_m2,
            "barometer_altitude_agl_m": sample.barometer_altitude_agl_m, "gps_altitude_agl_m": sample.gps_altitude_agl_m,
        }
        for prefix, values in (("position_enu_m", sample.position_enu_m), ("velocity_enu_mps", sample.velocity_enu_mps),
                               ("euler_rad", sample.euler_rad), ("omega_body_rad_s", sample.omega_body_rad_s),
                               ("canard_rad", sample.canard_deflection_rad), ("pid", sample.pid_output),
                               ("wind_enu_mps", sample.wind_enu_mps), ("relative_velocity_enu_mps", sample.relative_velocity_enu_mps),
                               ("controller_error_rad", sample.controller_error_rad), ("inertia_kg_m2", sample.inertia_kg_m2),
                               ("imu_acceleration_body_mps2", sample.imu_acceleration_body_mps2)):
            row.update({f"{prefix}_{index}": value for index, value in enumerate(_vector(values))})
        rows.append(row)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["time_s"])
        writer.writeheader(); writer.writerows(rows)
    saved_manifest = {**manifest, "classification": result.classification, "events": list(result.events), "exported_utc": stamp}
    manifest_path.write_text(json.dumps(saved_manifest, indent=2), encoding="utf-8")
    outputs = {"csv": csv_path, "manifest": manifest_path}
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        parquet_path = directory / f"telemetry_{stamp}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), parquet_path)
        outputs["parquet"] = parquet_path
    except ImportError:
        pass
    return outputs
