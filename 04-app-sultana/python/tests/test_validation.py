from types import SimpleNamespace

import numpy as np

from app.services.validation import compare_result_to_telemetry


def _sample(time_s: float, altitude: float, speed: float, east: float, north: float, on_rail: bool) -> SimpleNamespace:
    return SimpleNamespace(time_s=time_s, altitude_agl_m=altitude, airspeed_mps=speed, position_enu_m=np.array([east, north, altitude]), on_rail=on_rail)


def test_comparison_reports_metrics_without_mutating_the_flight_log(tmp_path) -> None:
    telemetry = tmp_path / "flight.csv"
    original = "time_s,altitude_agl_m,airspeed_mps,position_enu_m_0,position_enu_m_1\n0,0,0,0,0\n2,100,40,10,20\n4,0,20,30,40\n"
    telemetry.write_text(original, encoding="utf-8")
    result = SimpleNamespace(samples=[_sample(0, 0, 0, 0, 0, True), _sample(2, 110, 42, 12, 18, False), _sample(4, 0, 20, 31, 41, False)])
    comparison = compare_result_to_telemetry(result, telemetry)
    assert comparison.error["apogee_agl_m"] == 10.0
    assert comparison.error["landing_east_m"] == 1.0
    assert telemetry.read_text(encoding="utf-8") == original
