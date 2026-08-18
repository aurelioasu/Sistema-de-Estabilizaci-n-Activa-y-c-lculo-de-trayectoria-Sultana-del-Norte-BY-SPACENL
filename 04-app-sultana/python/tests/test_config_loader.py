from pathlib import Path

import pytest

from app.services.config_loader import ConfigValidationError, load_aero_table, load_mass_curve, load_scenario, load_thrust_curve, motor_options


ROOT = Path(__file__).resolve().parents[2]


def test_default_scenario_is_valid_and_preliminary() -> None:
    scenario = load_scenario(ROOT / "configs/vehicle/sultana_4canard.yaml", ROOT / "configs/environments/guadalupe_example.yaml")
    assert not scenario.calibration_complete
    assert len(load_thrust_curve(scenario)) >= 2
    assert len(load_mass_curve(scenario)) >= 2
    assert len(load_aero_table(scenario)) >= 2
    assert scenario.parameter_registry and scenario.parameter_registry["thrust_curve"]["status"] == "estimated"


def test_motor_catalogue_selects_matching_curve_and_scales_mass_profile() -> None:
    scenario = load_scenario(ROOT / "configs/vehicle/sultana_4canard.yaml", ROOT / "configs/environments/guadalupe_example.yaml")
    assert [identifier for identifier, _label in motor_options(scenario)] == ["knsb_10cm", "knsb_15cm", "knsb_20cm"]
    scenario.vehicle["propulsion"]["selected_motor_id"] = "knsb_10cm"
    assert load_thrust_curve(scenario)[-1] == (1.0, 0.0)
    assert load_mass_curve(scenario)[0][1] == pytest.approx(0.125)


def test_four_canards_are_required(tmp_path: Path) -> None:
    vehicle = (ROOT / "configs/vehicle/sultana_4canard.yaml").read_text(encoding="utf-8").replace("count: 4", "count: 2")
    vehicle_path = tmp_path / "vehicle.yaml"; vehicle_path.write_text(vehicle, encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="four canards"):
        load_scenario(vehicle_path, ROOT / "configs/environments/guadalupe_example.yaml")
