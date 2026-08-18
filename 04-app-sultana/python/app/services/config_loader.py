from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigValidationError(ValueError):
    """Raised before untrusted configuration reaches the simulation core."""


@dataclass(frozen=True)
class LoadedScenario:
    vehicle: dict[str, Any]
    environment: dict[str, Any]
    vehicle_path: Path
    environment_path: Path
    parameter_registry_path: Path | None = None
    parameter_registry: dict[str, Any] | None = None

    @property
    def calibration_complete(self) -> bool:
        return all(bool(value) for value in self.vehicle["calibration"].values())


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigValidationError(f"Configuration file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigValidationError(f"{path.name} must contain a YAML mapping")
    return data


def _require(mapping: dict[str, Any], key: str, section: str) -> Any:
    if key not in mapping:
        raise ConfigValidationError(f"Missing '{section}.{key}'")
    return mapping[key]


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigValidationError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ConfigValidationError(f"{name} must be finite")
    return number


def _positive(value: Any, name: str, allow_zero: bool = False) -> float:
    number = _finite(value, name)
    if number < 0 if allow_zero else number <= 0:
        comparison = "non-negative" if allow_zero else "positive"
        raise ConfigValidationError(f"{name} must be finite and {comparison}")
    return number


def _vec3(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ConfigValidationError(f"{name} must be a list of three SI values")
    return [_finite(component, f"{name}[{index}]") for index, component in enumerate(value)]


def _resolve_data_path(scenario: LoadedScenario, candidate: str) -> Path:
    path = Path(candidate)
    if path.is_absolute():
        return path
    # Built-in scenarios live under configs/{vehicle,environments}; laboratory
    # scenarios live under configs/scenarios/<name>.  Locate the repository by
    # its stable configs+data markers instead of assuming a fixed depth.
    for parent in (scenario.vehicle_path.parent, *scenario.vehicle_path.parents):
        if (parent / "configs").is_dir() and (parent / "data").is_dir():
            return parent / path
    return scenario.vehicle_path.parents[2] / path


def _load_parameter_registry(path: Path) -> dict[str, Any]:
    data = _read_yaml(path)
    parameters = _require(data, "parameters", "parameter_registry")
    if not isinstance(parameters, dict):
        raise ConfigValidationError("parameter_registry.parameters must be a mapping")
    required_fields = {"unit", "source", "date", "uncertainty", "status"}
    allowed_statuses = {"measured", "estimated", "pending"}
    for key, record in parameters.items():
        missing = required_fields.difference(record) if isinstance(record, dict) else required_fields
        if missing:
            raise ConfigValidationError(f"parameter_registry.{key} must include unit, source, date, uncertainty and status")
        if record["status"] not in allowed_statuses:
            raise ConfigValidationError(f"parameter_registry.{key}.status must be measured, estimated or pending")
        _finite(record["uncertainty"], f"parameter_registry.{key}.uncertainty")
    return parameters


def load_scenario(vehicle_path: str | Path, environment_path: str | Path) -> LoadedScenario:
    vehicle_file, environment_file = Path(vehicle_path), Path(environment_path)
    vehicle, environment = _read_yaml(vehicle_file), _read_yaml(environment_file)
    for section in ("geometry", "mass", "aerodynamics", "actuators", "recovery", "propulsion", "calibration"):
        section_data = _require(vehicle, section, "vehicle")
        if not isinstance(section_data, dict):
            raise ConfigValidationError(f"vehicle.{section} must be a mapping")
    for key in ("diameter_m", "reference_area_m2", "body_length_m", "cp_m"):
        _positive(_require(vehicle["geometry"], key, "geometry"), f"geometry.{key}")
    for key in ("dry_mass_kg", "propellant_mass_kg", "cg_dry_m", "cg_wet_m"):
        _positive(_require(vehicle["mass"], key, "mass"), f"mass.{key}", allow_zero=key == "propellant_mass_kg")
    for field in ("inertia_dry_kg_m2", "inertia_wet_kg_m2"):
        if any(value <= 0 for value in _vec3(_require(vehicle["mass"], field, "mass"), f"mass.{field}")):
            raise ConfigValidationError(f"mass.{field} values must be positive")
    if int(_require(vehicle["actuators"], "count", "actuators")) != 4:
        raise ConfigValidationError("v1 requires exactly four canards")
    for key in ("burn_time_s",):
        _positive(_require(vehicle["propulsion"], key, "propulsion"), f"propulsion.{key}")
    if not isinstance(_require(vehicle["propulsion"], "thrust_curve_csv", "propulsion"), str):
        raise ConfigValidationError("propulsion.thrust_curve_csv must be a relative CSV path")
    motors = vehicle["propulsion"].get("motors", [])
    if motors:
        if not isinstance(motors, list) or not all(isinstance(motor, dict) for motor in motors):
            raise ConfigValidationError("propulsion.motors must be a list of motor mappings")
        motor_ids = {str(motor.get("id", "")) for motor in motors}
        selected = str(vehicle["propulsion"].get("selected_motor_id", ""))
        if not selected or selected not in motor_ids:
            raise ConfigValidationError("propulsion.selected_motor_id must identify one configured motor")
        for motor in motors:
            for key in ("id", "label", "thrust_curve_csv"):
                if not isinstance(motor.get(key), str) or not motor[key]:
                    raise ConfigValidationError(f"motor.{key} must be a non-empty string")
            for key in ("grain_length_m", "propellant_mass_kg", "burn_time_s"):
                _positive(motor.get(key), f"motor.{key}")
    rocketcea = vehicle["propulsion"].get("rocketcea")
    if rocketcea is not None:
        if not isinstance(rocketcea, dict):
            raise ConfigValidationError("propulsion.rocketcea must be a mapping")
        for key in ("chamber_pressure_pa", "expansion_ratio", "catalyst_mass_g"):
            _positive(_require(rocketcea, key, "propulsion.rocketcea"), f"propulsion.rocketcea.{key}", allow_zero=key == "catalyst_mass_g")
    for section, field in (("mass", "mass_curve_csv"), ("aerodynamics", "aero_table_csv")):
        if field in vehicle[section] and not isinstance(vehicle[section][field], str):
            raise ConfigValidationError(f"{section}.{field} must be a CSV path")
    for status in ("thrust_curve", "mass_properties", "aerodynamics"):
        if not isinstance(_require(vehicle["calibration"], status, "calibration"), bool):
            raise ConfigValidationError(f"calibration.{status} must be boolean")

    for section in ("launch_site", "weather", "controller"):
        if not isinstance(_require(environment, section, "environment"), dict):
            raise ConfigValidationError(f"environment.{section} must be a mapping")
    for key in ("latitude_deg", "longitude_deg", "altitude_msl_m"):
        _finite(_require(environment["launch_site"], key, "launch_site"), f"launch_site.{key}")
    _vec3(_require(environment["weather"], "mean_wind_enu_mps", "weather"), "weather.mean_wind_enu_mps")
    if "profile_csv" in environment["weather"] and not isinstance(environment["weather"]["profile_csv"], str):
        raise ConfigValidationError("weather.profile_csv must be a CSV path")
    scenario = LoadedScenario(vehicle, environment, vehicle_file.resolve(), environment_file.resolve())
    registry_path = vehicle.get("parameter_registry_yaml")
    if registry_path:
        if not isinstance(registry_path, str):
            raise ConfigValidationError("parameter_registry_yaml must be a YAML path")
        resolved = _resolve_data_path(scenario, registry_path)
        registry = _load_parameter_registry(resolved)
        required = {"dry_mass_kg", "propellant_mass_kg", "cg_dry_m", "cg_wet_m", "inertia_dry_kg_m2", "inertia_wet_kg_m2", "thrust_curve", "cd_table"}
        missing = required.difference(registry)
        if missing:
            raise ConfigValidationError(f"parameter registry missing traceability for: {', '.join(sorted(missing))}")
        scenario = LoadedScenario(vehicle, environment, vehicle_file.resolve(), environment_file.resolve(), resolved, registry)
    return scenario


def selected_motor(scenario: LoadedScenario) -> dict[str, Any] | None:
    """Return the selected catalogue motor, or ``None`` for legacy scenarios."""
    propulsion = scenario.vehicle["propulsion"]
    motor_id = propulsion.get("selected_motor_id")
    for motor in propulsion.get("motors", []):
        if motor.get("id") == motor_id:
            return motor
    return None


def motor_options(scenario: LoadedScenario) -> list[tuple[str, str]]:
    return [(str(motor["id"]), str(motor["label"])) for motor in scenario.vehicle["propulsion"].get("motors", [])]


def load_thrust_curve(scenario: LoadedScenario) -> list[tuple[float, float]]:
    motor = selected_motor(scenario)
    source = motor["thrust_curve_csv"] if motor else scenario.vehicle["propulsion"]["thrust_curve_csv"]
    path = _resolve_data_path(scenario, source)
    if not path.is_file():
        raise ConfigValidationError(f"Thrust curve not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    curve = [(_positive(row.get("time_s"), "thrust time", allow_zero=True), _positive(row.get("thrust_n"), "thrust", allow_zero=True)) for row in rows]
    if len(curve) < 2 or any(right[0] <= left[0] for left, right in zip(curve, curve[1:])):
        raise ConfigValidationError("Thrust curve must have at least two strictly increasing time samples")
    return curve


def _load_csv(path: Path, expected: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ConfigValidationError(f"Data file not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or not expected.issubset(rows[0]):
        raise ConfigValidationError(f"{path.name} must include: {', '.join(sorted(expected))}")
    return rows


def load_mass_curve(scenario: LoadedScenario) -> list[tuple[float, float, float, list[float]]]:
    inline = scenario.vehicle["mass"].get("mass_curve_inline")
    if inline is not None:
        if not isinstance(inline, list) or any(not isinstance(row, (list, tuple)) or len(row) != 6 for row in inline):
            raise ConfigValidationError("Inline mass curve must have two or more increasing six-value rows")
        curve = [(
            _positive(row[0], "mass time", allow_zero=True),
            _positive(row[1], "propellant mass", allow_zero=True),
            _positive(row[2], "mass CG", allow_zero=True),
            [_positive(value, "mass inertia") for value in row[3:6]],
        ) for row in inline]
        if len(curve) < 2 or any(
            right[0] <= left[0] for left, right in zip(curve, curve[1:])
        ):
            raise ConfigValidationError("Inline mass curve must have two or more increasing six-value rows")
        return curve
    candidate = scenario.vehicle["mass"].get("mass_curve_csv")
    if not candidate:
        return []
    rows = _load_csv(_resolve_data_path(scenario, candidate), {"time_s", "propellant_mass_kg", "cg_m", "ixx_kg_m2", "iyy_kg_m2", "izz_kg_m2"})
    curve = [(
        _positive(row["time_s"], "mass time", allow_zero=True), _positive(row["propellant_mass_kg"], "propellant mass", allow_zero=True),
        _positive(row["cg_m"], "mass CG", allow_zero=True),
        [_positive(row[field], f"mass {field}") for field in ("ixx_kg_m2", "iyy_kg_m2", "izz_kg_m2")],
    ) for row in rows]
    if len(curve) < 2 or any(right[0] <= left[0] for left, right in zip(curve, curve[1:])):
        raise ConfigValidationError("Mass curve must have at least two strictly increasing time samples")
    motor = selected_motor(scenario)
    if not motor:
        return curve
    mass = scenario.vehicle["mass"]
    base_propellant = float(mass["propellant_mass_kg"])
    scale = float(motor["propellant_mass_kg"]) / max(base_propellant, 1e-9)
    time_scale = float(motor["burn_time_s"]) / max(float(scenario.vehicle["propulsion"]["burn_time_s"]), 1e-9)
    dry_mass, dry_cg = float(mass["dry_mass_kg"]), float(mass["cg_dry_m"])
    wet_cg = float(mass["cg_wet_m"])
    propellant_cg = ((dry_mass + base_propellant) * wet_cg - dry_mass * dry_cg) / max(base_propellant, 1e-9)
    dry_inertia = [float(value) for value in mass["inertia_dry_kg_m2"]]
    scaled = []
    for time_s, propellant_mass, _cg, inertia in curve:
        current_propellant = propellant_mass * scale
        fraction = current_propellant / max(base_propellant, 1e-9)
        cg = (dry_mass * dry_cg + current_propellant * propellant_cg) / max(dry_mass + current_propellant, 1e-9)
        scaled_inertia = [dry + fraction * (value - dry) for dry, value in zip(dry_inertia, inertia)]
        scaled.append((time_s * time_scale, current_propellant, cg, scaled_inertia))
    return scaled


def load_aero_table(scenario: LoadedScenario) -> list[tuple[float, float, float]]:
    inline = scenario.vehicle["aerodynamics"].get("aero_curve_inline")
    if inline is not None:
        if not isinstance(inline, list) or any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in inline):
            raise ConfigValidationError("Inline aerodynamic curve must have two or more increasing three-value rows")
        curve = [(
            _positive(row[0], "aero Mach", allow_zero=True),
            _positive(row[1], "aero Cd"),
            _positive(row[2], "aero Cn alpha", allow_zero=True),
        ) for row in inline]
        if len(curve) < 2 or any(
            right[0] <= left[0] for left, right in zip(curve, curve[1:])
        ):
            raise ConfigValidationError("Inline aerodynamic curve must have two or more increasing three-value rows")
        return curve
    candidate = scenario.vehicle["aerodynamics"].get("aero_table_csv")
    if not candidate:
        return []
    rows = _load_csv(_resolve_data_path(scenario, candidate), {"mach", "cd", "cn_alpha_per_rad"})
    curve = [(_positive(row["mach"], "aero Mach", allow_zero=True), _positive(row["cd"], "aero Cd"), _positive(row["cn_alpha_per_rad"], "aero Cn alpha", allow_zero=True)) for row in rows]
    if len(curve) < 2 or any(right[0] <= left[0] for left, right in zip(curve, curve[1:])):
        raise ConfigValidationError("Aerodynamic table must have at least two strictly increasing Mach samples")
    return curve


def load_atmosphere_profile(scenario: LoadedScenario) -> list[tuple[float, float, float, float, list[float]]]:
    candidate = scenario.environment["weather"].get("profile_csv")
    if not candidate:
        return []
    path = _resolve_data_path(scenario, candidate)
    rows = _load_csv(path, {"altitude_agl_m", "temperature_k", "pressure_pa", "relative_humidity", "wind_east_mps", "wind_north_mps", "wind_up_mps"})
    profile = [(
        _positive(row["altitude_agl_m"], "profile altitude", allow_zero=True), _positive(row["temperature_k"], "profile temperature"),
        _positive(row["pressure_pa"], "profile pressure"), _positive(row["relative_humidity"], "profile humidity", allow_zero=True),
        [_finite(row[field], f"profile {field}") for field in ("wind_east_mps", "wind_north_mps", "wind_up_mps")],
    ) for row in rows]
    if len(profile) < 2 or any(right[0] <= left[0] for left, right in zip(profile, profile[1:])):
        raise ConfigValidationError("Atmosphere profile must have at least two strictly increasing altitude samples")
    return profile


def build_core_config(scenario: LoadedScenario, duration_s: float, request_prediction: bool):
    """Map validated YAML values into the strongly typed pybind11 DTOs."""
    try:
        import numpy as np
        import sultana_core
    except ImportError as exc:  # pragma: no cover - depends on local C++ build
        raise RuntimeError("sultana_core is not built. Follow README.md to configure CMake.") from exc

    cfg = sultana_core.SimulationConfig()
    geometry, mass = scenario.vehicle["geometry"], scenario.vehicle["mass"]
    aero, actuators, recovery = scenario.vehicle["aerodynamics"], scenario.vehicle["actuators"], scenario.vehicle["recovery"]
    propulsion, status = scenario.vehicle["propulsion"], scenario.vehicle["calibration"]
    site, weather, controller = scenario.environment["launch_site"], scenario.environment["weather"], scenario.environment["controller"]
    for field in ("latitude_deg", "longitude_deg", "altitude_msl_m"):
        setattr(cfg.launch_site, field, float(site[field]))
    mapping = {**geometry, **mass, **aero, **recovery}
    for field, value in mapping.items():
        if hasattr(cfg.vehicle, field):
            setattr(cfg.vehicle, field, value)
    cfg.vehicle.max_canard_deflection_rad = math.radians(float(actuators["max_canard_deflection_deg"]))
    cfg.vehicle.max_canard_rate_rad_s = math.radians(float(actuators["max_canard_rate_deg_s"]))
    for field in ("canard_command_delay_s", "canard_mount_offset_rad"):
        if field in actuators and hasattr(cfg.vehicle, field):
            setattr(cfg.vehicle, field, np.array(actuators[field], dtype=float) if field.endswith("_rad") else float(actuators[field]))
    motor = selected_motor(scenario)
    cfg.vehicle.burn_time_s = float(motor["burn_time_s"] if motor else propulsion["burn_time_s"])
    if motor:
        cfg.vehicle.propellant_mass_kg = float(motor["propellant_mass_kg"])
    cfg.vehicle.inertia_dry_kg_m2 = np.array(mass["inertia_dry_kg_m2"], dtype=float)
    cfg.vehicle.inertia_wet_kg_m2 = np.array(mass["inertia_wet_kg_m2"], dtype=float)
    # Reassigning is required because pybind11 converts vectors at the boundary.
    curve = []
    for time_s, thrust_n in load_thrust_curve(scenario):
        point = sultana_core.ThrustPoint(); point.time_s, point.thrust_n = time_s, thrust_n; curve.append(point)
    cfg.vehicle.thrust_curve = curve
    mass_curve = []
    for time_s, propellant_mass_kg, cg_m, inertia in load_mass_curve(scenario):
        point = sultana_core.MassPoint(); point.time_s, point.propellant_mass_kg, point.cg_m = time_s, propellant_mass_kg, cg_m; point.inertia_kg_m2 = np.array(inertia, dtype=float); mass_curve.append(point)
    cfg.vehicle.mass_curve = mass_curve
    aero_curve = []
    for mach, cd, cn_alpha in load_aero_table(scenario):
        point = sultana_core.AeroPoint(); point.mach, point.cd, point.cn_alpha_per_rad = mach, cd, cn_alpha; aero_curve.append(point)
    cfg.vehicle.aero_curve = aero_curve
    cfg.vehicle.calibration.thrust_curve = status["thrust_curve"]
    cfg.vehicle.calibration.mass_properties = status["mass_properties"]
    cfg.vehicle.calibration.aerodynamics = status["aerodynamics"]
    for field, value in weather.items():
        if hasattr(cfg.environment, field):
            setattr(cfg.environment, field, np.array(value, dtype=float) if field == "mean_wind_enu_mps" else value)
    profile = []
    for altitude, temperature, pressure, humidity, wind in load_atmosphere_profile(scenario):
        point = sultana_core.AtmospherePoint(); point.altitude_agl_m, point.temperature_k, point.pressure_pa, point.relative_humidity = altitude, temperature, pressure, humidity; point.wind_enu_mps = np.array(wind, dtype=float); profile.append(point)
    cfg.environment.profile = profile
    for field, value in controller.items():
        if hasattr(cfg.controller, field):
            setattr(cfg.controller, field, value)
    for field, value in scenario.environment.get("rail", {}).items():
        if hasattr(cfg.rail, field):
            setattr(cfg.rail, field, math.radians(float(value)) if field.endswith("_deg") else value)
    if "elevation_deg" in scenario.environment.get("rail", {}):
        cfg.rail.elevation_rad = math.radians(float(scenario.environment["rail"]["elevation_deg"]))
    if "azimuth_deg" in scenario.environment.get("rail", {}):
        cfg.rail.azimuth_rad = math.radians(float(scenario.environment["rail"]["azimuth_deg"]))
    for field, value in scenario.environment.get("sensors", {}).items():
        if hasattr(cfg.sensors, field):
            setattr(cfg.sensors, field, value)
    cfg.duration_s = _positive(duration_s, "duration_s")
    cfg.request_flight_prediction = bool(request_prediction)
    return cfg
