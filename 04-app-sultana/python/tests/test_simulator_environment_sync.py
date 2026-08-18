"""Regression test for the launch-button weather synchronization."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QWidget

from app.services.weather import WeatherProfile
from app.ui.cfd_tab import CfdTab
from app.ui import simulator_tab


class _MapStub(QWidget):
    location_selected = Signal(float, float)

    def set_launch_site(self, latitude: float, longitude: float, name: str = "") -> None:
        self.launch_site = (latitude, longitude, name)


class _ViewportStub(QWidget):
    pass


class _ValueStub:
    def __init__(self) -> None:
        self.current = 0.0

    def setValue(self, value: float) -> None:
        self.current = value


def test_environment_sync_does_not_emit_a_user_weather_change(monkeypatch) -> None:
    """Coordinates supplied by the weather service must leave launch enabled."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(simulator_tab, "MapWidget", _MapStub)
    monkeypatch.setattr(simulator_tab, "RocketViewport", _ViewportStub)
    tab = simulator_tab.SimulatorTab()
    changed: list[bool] = []
    tab.weather_input_changed.connect(lambda: changed.append(True))
    weather = SimpleNamespace(
        mean_wind_enu_mps=(1.0, -2.0, 0.0),
        source="open_meteo",
        surface_temperature_k=298.15,
        surface_pressure_pa=90000.0,
        rain_rate_mm_h=0.0,
    )

    tab.set_environment("Punto de prueba", 25.6915204, -100.3972784, 1321.0, weather)
    tab.set_weather_ready(True)

    assert changed == []
    assert tab.launch_button.isEnabled()
    assert tab.current_selection()[:2] == (25.69152, -100.397278)
    app.processEvents()


def test_real_weather_profile_can_sync_to_cfd_without_turbulence_attribute() -> None:
    """Open-Meteo profiles do not carry a CFD turbulence field."""
    control = _ValueStub()
    tab = SimpleNamespace(
        _environment={"weather": {"turbulence_intensity_mps": 1.5}},
        _controls={
            "weather.surface_temperature_k": _ValueStub(),
            "weather.surface_pressure_pa": _ValueStub(),
            "weather.humidity_ratio": _ValueStub(),
        },
        rain=_ValueStub(),
        speed=control,
        _preview=lambda: None,
    )
    weather = WeatherProfile(
        source="open_meteo",
        surface_temperature_k=298.15,
        surface_pressure_pa=90000.0,
        humidity_ratio=0.42,
        mean_wind_enu_mps=(3.0, 4.0, 0.0),
        rain_rate_mm_h=2.0,
        raw={},
    )

    CfdTab.apply_weather_profile(tab, weather)

    # The CFD inlet is vehicle-relative airspeed; importing weather must not
    # overwrite it with the magnitude of the ambient wind.
    assert control.current == 0.0
    assert tab._environment["weather"]["turbulence_intensity_mps"] == 1.5
