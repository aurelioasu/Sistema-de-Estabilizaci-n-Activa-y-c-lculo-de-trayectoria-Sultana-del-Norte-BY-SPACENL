from __future__ import annotations

import asyncio
import threading
from datetime import date, datetime

from PySide6.QtCore import QObject, Signal, Slot

from app.services.location import ElevationService, GeocodedLocation
from app.services.terrain import TerrainMapService
from app.services.weather import WeatherService
from app.services.monte_carlo import run_monte_carlo
from app.services.cfd import prepare_case, run_case


class SimulationWorker(QObject):
    """Owns the native call; UI objects must remain in the GUI thread."""

    completed = Signal(object)
    failed = Signal(str)

    @Slot(object)
    def run(self, config: object) -> None:
        try:
            import sultana_core
            self.completed.emit(sultana_core.run_simulation(config))
        except Exception as exc:  # UI boundary: display errors instead of crashing Qt loop
            self.failed.emit(str(exc))


class CfdWorker(QObject):
    """Runs OpenFOAM outside the GUI thread and keeps a cancellable boundary."""
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._cancel = threading.Event()
        self._root: object | None = None
        self._request: object | None = None

    def set_job(self, root: object, request: object) -> None:
        self._root, self._request = root, request

    @Slot()
    def run(self) -> None:
        try:
            if self._root is None or self._request is None:
                raise RuntimeError("No se configuró el caso CFD")
            case_dir = prepare_case(self._root, self._request)
            self.completed.emit(run_case(case_dir, self._request, self.progress.emit, self._cancel))
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot()
    def cancel(self) -> None:
        self._cancel.set()


class EnvironmentWorker(QObject):
    """Retrieves weather and terrain for the coordinates selected on the map."""

    completed = Signal(object, object, object)
    failed = Signal(str)

    @Slot(str, float, float, object)
    def load(self, name: str, latitude: float, longitude: float, when: datetime | date) -> None:
        try:
            location, weather, terrain = asyncio.run(self._load(name, latitude, longitude, when))
            self.completed.emit(location, weather, terrain)
        except Exception as exc:
            self.failed.emit(str(exc))

    @staticmethod
    async def _load(name: str, latitude: float, longitude: float, when: datetime | date):
        elevation, weather, terrain = await asyncio.gather(
            ElevationService().fetch(latitude, longitude),
            WeatherService().fetch(latitude, longitude, when),
            TerrainMapService().fetch(latitude, longitude),
            return_exceptions=True,
        )
        if isinstance(elevation, Exception):
            raise elevation
        if isinstance(weather, Exception):
            raise weather
        location = GeocodedLocation(
            name=name.strip() or f"Punto seleccionado: {latitude:.5f}, {longitude:.5f}",
            latitude_deg=latitude,
            longitude_deg=longitude,
            elevation_m=float(elevation),
        )
        return location, weather, None if isinstance(terrain, Exception) else terrain


class MapTerrainWorker(QObject):
    """Refreshes the 3D texture and MSL elevation after the user moves the map."""

    completed = Signal(float, float, float, object)
    failed = Signal(str)

    @Slot(float, float, float)
    def load(self, latitude: float, longitude: float, zoom: float) -> None:
        try:
            elevation, terrain = asyncio.run(self._load(latitude, longitude, zoom))
            self.completed.emit(latitude, longitude, elevation, terrain)
        except Exception as exc:
            self.failed.emit(str(exc))

    @staticmethod
    async def _load(latitude: float, longitude: float, zoom: float = 17.0):
        elevation, terrain = await asyncio.gather(
            ElevationService().fetch(latitude, longitude),
            TerrainMapService().fetch(latitude, longitude, zoom=zoom),
        )
        return elevation, terrain


class MonteCarloWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    @Slot(object, object, int, object, float)
    def run(self, scenario: object, weather_profile: object, runs: int, launch_site: object, wind_sigma_mps: float) -> None:
        try:
            self.completed.emit(
                run_monte_carlo(scenario, weather_profile, runs, launch_site=launch_site,
                                wind_sigma_mps=wind_sigma_mps, progress=self.progress.emit)
            )
        except Exception as exc:
            self.failed.emit(str(exc))
