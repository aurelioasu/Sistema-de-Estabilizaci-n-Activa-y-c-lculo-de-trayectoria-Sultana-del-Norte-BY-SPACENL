from pathlib import Path

import pytest

pytest.importorskip("rocketcea")

from app.services.config_loader import load_scenario
from app.services.rocketcea_motor import rocketcea_motor_report


ROOT = Path(__file__).resolve().parents[2]


def test_rocketcea_report_preserves_declared_composition_and_catalogue_sizes() -> None:
    scenario = load_scenario(
        ROOT / "configs/vehicle/sultana_4canard.yaml",
        ROOT / "configs/environments/guadalupe_example.yaml",
    )
    reports = [rocketcea_motor_report(scenario, motor_id) for motor_id in ("knsb_10cm", "knsb_15cm", "knsb_20cm")]
    assert [report.motor_id for report in reports] == ["knsb_10cm", "knsb_15cm", "knsb_20cm"]
    assert reports[0].fe2o3_mass_fraction == pytest.approx(0.008)
    assert reports[-1].sorbitol_mass_fraction == pytest.approx(0.6972)
    assert reports[-1].kno3_mass_fraction == pytest.approx(0.2988)
    assert all(report.ideal_isp_s > 0 and report.cstar_m_s > 0 and report.chamber_temperature_k > 0 for report in reports)
    assert "no sustituye curva de empuje medida" in reports[-1].status
