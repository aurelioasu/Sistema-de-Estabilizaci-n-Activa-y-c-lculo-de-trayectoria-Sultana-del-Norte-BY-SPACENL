from pathlib import Path

import numpy as np

from app.services.config_loader import build_core_config, load_scenario
from app.services.monte_carlo import MonteCarloSummary, ellipse_dimensions_label, prepare_monte_carlo_config, run_monte_carlo
from app.services.weather import WeatherProfile
from app.services.weather_application import apply_weather_profile


ROOT = Path(__file__).resolve().parents[2]


def _weather(wind: tuple[float, float, float]) -> WeatherProfile:
    return WeatherProfile("open_meteo", 293.15, 100000.0, 0.5, wind, 0.2, {}, 0.002)


def _scenario():
    return load_scenario(ROOT / "configs/vehicle/sultana_4canard.yaml", ROOT / "configs/environments/guadalupe_example.yaml")


def test_monte_carlo_summary_manifest_uses_semi_axes() -> None:
    summary = MonteCarloSummary(
        runs=100, apogee_p95_m=(100.0, 200.0), max_speed_p95_mps=(50.0, 70.0),
        landing_center_enu_m=(10.0, 20.0), landing_semi_axes_95_m=(30.0, 15.0, 45.0),
        descent_time_p95_s=(20.0, 30.0), sensitivities={"wind_east": 0.8}, primary_dispersion_cause="wind_east",
        seed=123, uncertainties={"wind_east_std_mps": 1.5, "wind_north_std_mps": 1.5},
    )
    manifest = summary.to_manifest()
    assert manifest["landing_semi_axes_95_m"] == [30.0, 15.0, 45.0]
    assert "landing_ellipse_95_m" not in manifest
    assert ellipse_dimensions_label((563.0, 72.0, 0.0)) == "Elipse 95% — semiejes: 563 × 72 m; extensión total: 1126 × 144 m"


def test_nominal_and_monte_carlo_baseline_apply_the_same_weather() -> None:
    scenario, weather = _scenario(), _weather((4.0, -7.0, 0.0))
    nominal = build_core_config(scenario, 180.0, False)
    apply_weather_profile(nominal, weather)
    monte = prepare_monte_carlo_config(scenario, weather, (25.0, -100.0, 600.0))
    assert np.allclose(nominal.environment.mean_wind_enu_mps, monte.environment.mean_wind_enu_mps)
    assert nominal.environment.surface_temperature_k == monte.environment.surface_temperature_k
    assert nominal.environment.surface_pressure_pa == monte.environment.surface_pressure_pa
    assert nominal.environment.rain_cd_delta == monte.environment.rain_cd_delta


def test_distinct_open_meteo_profiles_produce_distinct_monte_carlo_base_winds() -> None:
    scenario = _scenario()
    first = prepare_monte_carlo_config(scenario, _weather((1.0, 2.0, 0.0)), (25.0, -100.0, 600.0))
    second = prepare_monte_carlo_config(scenario, _weather((-5.0, 8.0, 0.0)), (19.0, -99.0, 2200.0))
    assert np.allclose(first.environment.mean_wind_enu_mps, [1.0, 2.0, 0.0])
    assert np.allclose(second.environment.mean_wind_enu_mps, [-5.0, 8.0, 0.0])


def test_changing_only_wind_uncertainty_changes_the_ellipse() -> None:
    scenario = _scenario()
    scenario.environment["weather"]["turbulence_intensity_mps"] = 0.0
    weather = _weather((3.0, -1.0, 0.0))
    fixed = {key: 0.0 for key in ("thrust_scale_std", "dry_mass_scale_std", "propellant_scale_std", "cd_scale_std", "recovery_delay_std_s")}
    no_wind = run_monte_carlo(scenario, weather, runs=10, seed=42, wind_sigma_mps=0.0, uncertainty_overrides=fixed)
    wind = run_monte_carlo(scenario, weather, runs=10, seed=42, wind_sigma_mps=3.0, uncertainty_overrides=fixed)
    assert max(no_wind.landing_semi_axes_95_m[:2]) < 1e-8
    assert max(wind.landing_semi_axes_95_m[:2]) > 0.0
