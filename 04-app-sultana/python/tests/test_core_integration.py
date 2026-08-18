from pathlib import Path

from app.services.config_loader import build_core_config, load_scenario
from app.services.exporter import export_result


ROOT = Path(__file__).resolve().parents[2]


def test_python_binding_runs_deterministic_preliminary_simulation() -> None:
    import sultana_core

    scenario = load_scenario(
        ROOT / "configs/vehicle/sultana_4canard.yaml",
        ROOT / "configs/environments/guadalupe_example.yaml",
    )
    first = sultana_core.run_simulation(build_core_config(scenario, 2.1, request_prediction=True))
    second = sultana_core.run_simulation(build_core_config(scenario, 2.1, request_prediction=True))

    assert first.classification == "preliminary_analysis"
    assert not first.flight_prediction_allowed
    assert first.events[0].startswith("prediction_blocked")
    assert len(first.samples) > 100
    assert first.samples[-1].altitude_msl_m >= 500.0
    assert first.samples[-1].position_enu_m[2] == second.samples[-1].position_enu_m[2]


def test_result_export_writes_csv_manifest_and_parquet(tmp_path: Path) -> None:
    import sultana_core

    scenario = load_scenario(ROOT / "configs/vehicle/sultana_4canard.yaml", ROOT / "configs/environments/guadalupe_example.yaml")
    result = sultana_core.run_simulation(build_core_config(scenario, 0.2, request_prediction=False))
    outputs = export_result(result, tmp_path, {"scenario": "test"})
    assert outputs["csv"].is_file()
    assert outputs["manifest"].is_file()
    assert outputs["parquet"].is_file()


def test_core_exposes_aerodynamic_rain_and_canard_analysis() -> None:
    import sultana_core

    scenario = load_scenario(ROOT / "configs/vehicle/sultana_4canard.yaml", ROOT / "configs/environments/guadalupe_example.yaml")
    config = build_core_config(scenario, 3.0, request_prediction=False)
    config.environment.rain_rate_mm_h = 4.0
    config.environment.rain_cd_delta = 0.04
    result = sultana_core.run_simulation(config)
    sample = result.samples[-1]
    assert len(sample.canard_deflection_rad) == 4
    assert len(sample.wind_enu_mps) == 3
    assert sample.drag_force_n > 0.0
    assert sample.rain_impact_force_n > 0.0
    assert sample.air_temperature_k > 0.0
    assert sample.air_pressure_pa > 0.0
    assert 0.0 <= sample.air_relative_humidity <= 1.0
    assert sample.air_density_kg_m3 > 0.0
    assert sample.friction_heat_proxy >= 0.0
    assert sample.static_margin_calibers > 0.0
