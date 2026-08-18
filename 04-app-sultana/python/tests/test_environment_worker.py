import asyncio
from datetime import datetime

from app import workers


def test_map_terrain_worker_passes_the_map_zoom_to_the_mosaic(monkeypatch) -> None:
    calls: list[object] = []

    class FakeElevationService:
        async def fetch(self, latitude: float, longitude: float) -> float:
            return 612.5

    class FakeTerrainMapService:
        async def fetch(self, latitude: float, longitude: float, *, zoom: float) -> object:
            calls.append((latitude, longitude, zoom))
            return object()

    monkeypatch.setattr(workers, "ElevationService", FakeElevationService)
    monkeypatch.setattr(workers, "TerrainMapService", FakeTerrainMapService)

    asyncio.run(workers.MapTerrainWorker._load(20.123456, -103.654321, 16.2))

    assert calls == [(20.123456, -103.654321, 16.2)]


def test_environment_worker_uses_selected_map_coordinates(monkeypatch) -> None:
    calls: list[tuple[str, float, float]] = []
    weather = object()
    terrain = object()

    class FakeElevationService:
        async def fetch(self, latitude: float, longitude: float) -> float:
            calls.append(("elevation", latitude, longitude))
            return 612.5

    class FakeWeatherService:
        async def fetch(self, latitude: float, longitude: float, when: datetime) -> object:
            calls.append(("weather", latitude, longitude))
            return weather

    class FakeTerrainMapService:
        async def fetch(self, latitude: float, longitude: float) -> object:
            calls.append(("terrain", latitude, longitude))
            return terrain

    monkeypatch.setattr(workers, "ElevationService", FakeElevationService)
    monkeypatch.setattr(workers, "WeatherService", FakeWeatherService)
    monkeypatch.setattr(workers, "TerrainMapService", FakeTerrainMapService)

    location, loaded_weather, loaded_terrain = asyncio.run(
        workers.EnvironmentWorker._load("Punto de prueba", 20.123456, -103.654321, datetime(2026, 7, 29, 13, 10))
    )

    assert {(latitude, longitude) for _, latitude, longitude in calls} == {(20.123456, -103.654321)}
    assert location.name == "Punto de prueba"
    assert location.elevation_m == 612.5
    assert loaded_weather is weather
    assert loaded_terrain is terrain
