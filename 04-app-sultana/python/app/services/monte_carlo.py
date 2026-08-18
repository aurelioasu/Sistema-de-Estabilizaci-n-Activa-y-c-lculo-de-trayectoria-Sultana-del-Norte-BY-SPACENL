from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from .config_loader import LoadedScenario, build_core_config
from .weather import WeatherProfile
from .weather_application import apply_weather_profile


DEFAULT_UNCERTAINTIES = {
    "thrust_scale_std": 0.05, "dry_mass_scale_std": 0.02, "propellant_scale_std": 0.04,
    "cd_scale_std": 0.08, "wind_east_std_mps": 1.5, "wind_north_std_mps": 1.5,
    "recovery_delay_std_s": 0.15,
}


@dataclass(frozen=True)
class MonteCarloSummary:
    runs: int
    apogee_p95_m: tuple[float, float]
    max_speed_p95_mps: tuple[float, float]
    landing_center_enu_m: tuple[float, float]
    landing_semi_axes_95_m: tuple[float, float, float]
    descent_time_p95_s: tuple[float, float]
    sensitivities: dict[str, float]
    primary_dispersion_cause: str
    seed: int
    uncertainties: dict[str, float]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "runs": self.runs, "seed": self.seed, "uncertainties": self.uncertainties,
            "apogee_p95_m": list(self.apogee_p95_m), "max_speed_p95_mps": list(self.max_speed_p95_mps),
            "landing_center_enu_m": list(self.landing_center_enu_m),
            "landing_semi_axes_95_m": list(self.landing_semi_axes_95_m),
            "descent_time_p95_s": list(self.descent_time_p95_s), "sensitivities": self.sensitivities,
            "primary_dispersion_cause": self.primary_dispersion_cause,
        }


def _interval(values: list[float]) -> tuple[float, float]:
    return tuple(float(value) for value in np.percentile(values, (2.5, 97.5)))  # type: ignore[return-value]


def ellipse_dimensions_label(semi_axes_95_m: tuple[float, float, float]) -> str:
    major, minor, _ = semi_axes_95_m
    return f"Elipse 95% — semiejes: {major:.0f} × {minor:.0f} m; extensión total: {2 * major:.0f} × {2 * minor:.0f} m"


def prepare_monte_carlo_config(
    scenario: LoadedScenario, weather_profile: WeatherProfile, launch_site: tuple[float, float, float] | None,
):
    """Build one draw's baseline with precisely the same weather as nominal."""
    cfg = build_core_config(scenario, duration_s=180.0, request_prediction=False)
    if launch_site:
        cfg.launch_site.latitude_deg, cfg.launch_site.longitude_deg, cfg.launch_site.altitude_msl_m = launch_site
    apply_weather_profile(cfg, weather_profile)
    return cfg


def _safe_correlation(values: list[float], landing_distance: np.ndarray) -> float:
    if np.std(values) < 1e-12 or np.std(landing_distance) < 1e-12:
        return 0.0
    return float(np.corrcoef(values, landing_distance)[0, 1])


def run_monte_carlo(
    scenario: LoadedScenario, weather_profile: WeatherProfile, runs: int = 100, seed: int = 20260729,
    launch_site: tuple[float, float, float] | None = None, wind_sigma_mps: float = 1.5,
    uncertainty_overrides: dict[str, float] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> MonteCarloSummary:
    """Run reproducible draws around an explicit, unperturbed weather profile."""
    if not 10 <= runs <= 1000:
        raise ValueError("Monte Carlo requires between 10 and 1000 runs")
    if wind_sigma_mps < 0:
        raise ValueError("Wind uncertainty must be non-negative")
    uncertainties = {**DEFAULT_UNCERTAINTIES, "wind_east_std_mps": wind_sigma_mps, "wind_north_std_mps": wind_sigma_mps, **(uncertainty_overrides or {})}
    rng = np.random.default_rng(seed)
    apogees: list[float] = []
    speeds: list[float] = []
    descent_times: list[float] = []
    landings: list[list[float]] = []
    draws: dict[str, list[float]] = {key: [] for key in ("thrust", "dry_mass", "propellant", "cd", "wind_east", "wind_north", "recovery_delay")}
    for index in range(runs):
        cfg = prepare_monte_carlo_config(scenario, weather_profile, launch_site)
        perturbation = {
            "thrust": float(rng.normal(1.0, uncertainties["thrust_scale_std"])),
            "dry_mass": float(rng.normal(1.0, uncertainties["dry_mass_scale_std"])),
            "propellant": float(rng.normal(1.0, uncertainties["propellant_scale_std"])),
            "cd": float(rng.normal(1.0, uncertainties["cd_scale_std"])),
            "wind_east": float(rng.normal(0.0, uncertainties["wind_east_std_mps"])),
            "wind_north": float(rng.normal(0.0, uncertainties["wind_north_std_mps"])),
            "recovery_delay": float(rng.normal(0.0, uncertainties["recovery_delay_std_s"])),
        }
        for key, value in perturbation.items():
            draws[key].append(value)
        cfg.vehicle.dry_mass_kg *= perturbation["dry_mass"]
        cfg.vehicle.propellant_mass_kg *= perturbation["propellant"]
        mass_curve = []
        for point in cfg.vehicle.mass_curve:
            point.propellant_mass_kg *= perturbation["propellant"]
            mass_curve.append(point)
        cfg.vehicle.mass_curve = mass_curve
        cfg.vehicle.parachute_deploy_delay_s = max(0.0, cfg.vehicle.parachute_deploy_delay_s + perturbation["recovery_delay"])
        # East and north errors are independent; the Open-Meteo wind remains the base vector.
        cfg.environment.mean_wind_enu_mps = np.asarray(cfg.environment.mean_wind_enu_mps, dtype=float) + np.array([perturbation["wind_east"], perturbation["wind_north"], 0.0])
        cfg.environment.turbulence_seed = int(seed + index)
        thrust = []
        for point in cfg.vehicle.thrust_curve:
            point.thrust_n *= perturbation["thrust"]
            thrust.append(point)
        cfg.vehicle.thrust_curve = thrust
        aero = []
        for point in cfg.vehicle.aero_curve:
            point.cd *= perturbation["cd"]
            aero.append(point)
        cfg.vehicle.aero_curve = aero
        import sultana_core
        result = sultana_core.run_simulation(cfg)
        samples = result.samples
        apogees.append(max(float(sample.altitude_agl_m) for sample in samples))
        speeds.append(max(float(sample.airspeed_mps) for sample in samples))
        parachute = next((sample for sample in samples if sample.parachute_deployed), None)
        descent_times.append(float(samples[-1].time_s - parachute.time_s) if parachute else 0.0)
        landing = samples[-1].position_enu_m
        landings.append([float(landing[0]), float(landing[1])])
        if progress:
            progress(index + 1, runs)
    landing_array = np.asarray(landings)
    center = landing_array.mean(axis=0)
    covariance = np.cov(landing_array, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    semi_axes = 2.4477 * np.sqrt(np.maximum(eigenvalues[order], 0.0))
    major_vector = eigenvectors[:, order[0]]
    heading = float(np.degrees(np.arctan2(major_vector[1], major_vector[0])))
    landing_distance = np.linalg.norm(landing_array - center, axis=1)
    sensitivities = {key: _safe_correlation(values, landing_distance) for key, values in draws.items()}
    primary = max(sensitivities, key=lambda key: abs(sensitivities[key]))
    return MonteCarloSummary(
        runs, _interval(apogees), _interval(speeds), (float(center[0]), float(center[1])),
        (float(semi_axes[0]), float(semi_axes[1]), heading), _interval(descent_times), sensitivities,
        primary, seed, uncertainties,
    )
